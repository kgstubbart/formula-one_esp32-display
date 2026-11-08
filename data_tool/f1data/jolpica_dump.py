"""Read race data from Jolpica's CSV database dump instead of calling the API per race.

Building every race from 2002 to 2022 through the API would take about 5,000
requests, which is ten hours at Jolpica's 500 requests per hour. The dump is one
15 MB download with the same data.

The dump uses Jolpica's own schema, so this module converts its rows into the same
shapes that the API returns. The rest of the tool cannot tell which one it got.
"""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from .http_client import USER_AGENT, DataUnavailable
from .durations import parse_duration

DUMP_URL = "https://api.jolpi.ca/data/dumps/download/delayed/?dump_type=csv"
DUMP_FILE = "jolpica-f1-csv.zip"
MAX_AGE_S = 30 * 24 * 3600
RACE_SESSION = "R"
POSITION_TEXT = {"Withdrew": "W", "Disqualified": "D", "Did not start": "W",
                 "Did not qualify": "F", "Did not prequalify": "F", "Excluded": "E"}


def download(cache_dir: Path, log=print, force: bool = False) -> Path:
    """Fetch the dump if it is missing or a month old. Returns its path."""
    path = Path(cache_dir) / "jolpica_dump" / DUMP_FILE
    if path.exists() and not force and time.time() - path.stat().st_mtime < MAX_AGE_S:
        return path
    log("Downloading the Jolpica database dump (about 15 MB, once a month)...")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(DUMP_URL, headers={"User-Agent": USER_AGENT}, timeout=(15, 300))
        response.raise_for_status()
    except requests.RequestException as problem:
        if path.exists():
            log(f"  Could not refresh the dump ({problem.__class__.__name__}); using the old one.")
            return path
        raise DataUnavailable(f"Could not download the Jolpica database dump: {problem}")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(response.content)
    if not zipfile.is_zipfile(tmp):
        tmp.unlink()
        raise DataUnavailable("The Jolpica database dump download was not a valid zip file.")
    tmp.replace(path)
    # The download link names the day the dump was made, e.g. .../dumps/2026-10-24/...
    made = re.search(r"/dumps/(\d{4}-\d{2}-\d{2})/", response.url)
    path.with_suffix(".date").write_text(made.group(1) if made else "unknown date")
    return path


@dataclass
class DumpRace:
    results: list[dict]
    laps: dict[str, dict[int, tuple[float, int]]]
    pit_stops: dict[str, dict[int, float]]


class JolpicaDump:
    """Indexes the tables needed for race replays, loaded once per run."""

    def __init__(self, zip_path: Path):
        self.path = Path(zip_path)
        date_file = self.path.with_suffix(".date")
        self.date = date_file.read_text().strip() if date_file.exists() else "unknown date"
        with zipfile.ZipFile(self.path) as archive:
            def table(name: str):
                with archive.open(f"formula_one_{name}.csv") as raw:
                    yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))

            seasons = {row["id"]: int(row["year"]) for row in table("season")}
            self.rounds = {(seasons[row["season_id"]], int(row["number"])): row["id"]
                           for row in table("round") if row["is_cancelled"] != "t"}
            self.race_session = {row["round_id"]: row["id"] for row in table("session")
                                 if row["type"] == RACE_SESSION}
            drivers = {row["id"]: row for row in table("driver")}
            teams = {row["id"]: row for row in table("team")}
            team_drivers = {row["id"]: (drivers[row["driver_id"]], teams[row["team_id"]])
                            for row in table("teamdriver")}
            self.round_entries = {row["id"]: (row["car_number"], *team_drivers[row["team_driver_id"]])
                                  for row in table("roundentry")}
            self.entries: dict[str, list[dict]] = {}
            for row in table("sessionentry"):
                self.entries.setdefault(row["session_id"], []).append(row)
            self.laps: dict[str, list[tuple[int, float, int, str]]] = {}
            lap_numbers: dict[str, int] = {}
            for row in table("lap"):
                if not row["number"] or row["is_deleted"] == "t":
                    continue  # Rows without a number are fastest-lap records, not laps.
                seconds = parse_duration(row["time"])
                lap_numbers[row["id"]] = int(row["number"])
                if seconds is not None:
                    self.laps.setdefault(row["session_entry_id"], []).append(
                        (int(row["number"]), seconds, int(row["position"] or 0), row["id"]))
            self.pits: dict[str, list[tuple[int, float]]] = {}
            for row in table("pitstop"):
                lap = lap_numbers.get(row["lap_id"])
                if lap is not None:
                    self.pits.setdefault(row["session_entry_id"], []).append(
                        (lap, parse_duration(row["duration"]) or 0.0))

    def race(self, season: int, round_number: int) -> DumpRace | None:
        """The race in API shapes, or None if the dump does not have it."""
        round_id = self.rounds.get((season, round_number))
        session_id = self.race_session.get(round_id)
        entries = self.entries.get(session_id)
        if not entries:
            return None
        results, laps, pits = [], {}, {}
        for entry in sorted(entries, key=lambda e: int(e["position"] or 999)):
            number, driver, team = self.round_entries[entry["round_entry_id"]]
            driver_id = driver["reference"]
            classified = entry["is_classified"] == "t"
            status = entry["detail"] or ("Finished" if classified else "Retired")
            result = {
                "number": number, "position": entry["position"],
                "positionText": entry["position"] if classified else POSITION_TEXT.get(status, "R"),
                "status": status, "laps": entry["laps_completed"] or "0", "grid": entry["grid"],
                "Driver": {"driverId": driver_id, "code": driver["abbreviation"],
                           "givenName": driver["forename"], "familyName": driver["surname"]},
                "Constructor": {"constructorId": team["reference"], "name": team["name"]},
            }
            # Like the API, a total time only for cars that finished on the lead lap.
            if classified and entry["time"] and not status.startswith(("+", "Lapped")):
                result["Time"] = {"millis": str(int(parse_duration(entry["time"]) * 1000))}
            results.append(result)
            for lap, seconds, position, _ in self.laps.get(entry["id"], []):
                laps.setdefault(driver_id, {})[lap] = (seconds, position)
            for lap, seconds in self.pits.get(entry["id"], []):
                pits.setdefault(driver_id, {})[lap] = seconds
        return DumpRace(results, laps, pits)
