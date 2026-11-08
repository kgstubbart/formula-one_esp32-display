"""Typed access to Jolpica and OpenF1, and the rules for what each race can offer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .durations import parse_duration
from .http_client import CachedClient, DataUnavailable, RateLimiter, make_clients
from .jolpica_dump import JolpicaDump, download as download_dump

FIRST_LAP_TIMING_SEASON = 1996   # Jolpica: "Data starts from the 1996 season."
FIRST_PIT_STOP_SEASON = 2011     # Jolpica: "Data starts from the 2011 season."
FIRST_TELEMETRY_SEASON = 2023    # OpenF1: "from 2023 onwards"
PAGE_SIZE = 100                  # Jolpica maximum
RACE_DURATION_MARGIN = timedelta(hours=5)
DAY_S = 24 * 3600

AVAIL_TELEMETRY = "T"
AVAIL_ESTIMATED = "E"
AVAIL_UNAVAILABLE = "U"
AVAIL_FUTURE = "F"


def parse_time(text: str) -> float:
    """ISO 8601 UTC timestamp -> seconds since the Unix epoch."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def iso(epoch_s: float) -> str:
    moment = datetime.fromtimestamp(epoch_s, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


@dataclass
class RaceInfo:
    season: int
    round: int
    name: str
    circuit_id: str
    circuit_name: str
    date: str
    start_utc: float
    session: dict | None
    availability: str
    note: str

    @property
    def stem(self) -> str:
        return f"{self.season}_{self.round:02d}"


class Sources:
    def __init__(self, cache_dir: Path, log=print):
        self.log = log
        self.cache_dir = Path(cache_dir)
        self.openf1, self.jolpica = make_clients(self.cache_dir, log)
        self.raw = CachedClient("outlines", "https://raw.githubusercontent.com", self.cache_dir,
                                RateLimiter([(2, 1.0)]), log=log)
        self.use_dump = True
        self._dump: JolpicaDump | None = None

    def network_calls(self) -> int:
        return self.openf1.network_calls + self.jolpica.network_calls + self.raw.network_calls

    # ---- Jolpica ------------------------------------------------------------------------

    def _pages(self, path: str, ttl_s: float | None):
        offset = 0
        while True:
            data = self.jolpica.get(path, [("limit", "=", PAGE_SIZE), ("offset", "=", offset)],
                                    ttl_s)
            if not data:
                return
            mr = data["MRData"]
            yield mr
            offset += PAGE_SIZE
            if offset >= int(mr["total"]):
                return

    def seasons(self) -> list[int]:
        years: list[int] = []
        for mr in self._pages("seasons/", 7 * DAY_S):
            years += [int(s["season"]) for s in mr["SeasonTable"]["Seasons"]]
        return sorted(years)

    def _ttl(self, season: int) -> float | None:
        """Past seasons are final; the current one still changes."""
        return DAY_S if season >= datetime.now(timezone.utc).year - 1 else None

    def _race_rows(self, season: int) -> list[dict]:
        rows: list[dict] = []
        for mr in self._pages(f"{season}/races/", self._ttl(season)):
            rows += mr["RaceTable"]["Races"]
        return rows

    def _sessions(self, season: int) -> list[dict]:
        if season < FIRST_TELEMETRY_SEASON:
            return []
        return self.openf1.get("sessions", [("year", "=", season), ("session_name", "=", "Race")],
                               DAY_S)

    def races(self, season: int, now: float | None = None) -> list[RaceInfo]:
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        sessions = self._sessions(season)
        return [classify_race(row, sessions, now) for row in self._race_rows(season)]

    def race(self, season: int, round_number: int) -> RaceInfo:
        for info in self.races(season):
            if info.round == round_number:
                return info
        raise DataUnavailable(f"Jolpica lists no round {round_number} in the {season} season.")

    def _from_dump(self, race: RaceInfo):
        """Historic races come from the database dump, so they cost no API requests."""
        if not self.use_dump or race.season >= FIRST_TELEMETRY_SEASON:
            return None
        if self._dump is None:
            self._dump = JolpicaDump(download_dump(self.cache_dir, self.log))
        found = self._dump.race(race.season, race.round)
        return found if found is not None and found.laps else None

    def data_origin(self, race: RaceInfo) -> str:
        dumped = self._from_dump(race)
        return f"database dump of {self._dump.date}" if dumped is not None else "API"

    def results(self, race: RaceInfo) -> list[dict]:
        dumped = self._from_dump(race)
        if dumped is not None:
            return dumped.results
        rows: list[dict] = []
        for mr in self._pages(f"{race.season}/{race.round}/results/", self._ttl(race.season)):
            for entry in mr["RaceTable"]["Races"]:
                rows += entry["Results"]
        return rows

    def lap_times(self, race: RaceInfo) -> dict[str, dict[int, tuple[float, int]]]:
        """driverId -> {lap: (seconds, position)}."""
        dumped = self._from_dump(race)
        if dumped is not None:
            return dumped.laps
        laps: dict[str, dict[int, tuple[float, int]]] = {}
        for mr in self._pages(f"{race.season}/{race.round}/laps/", self._ttl(race.season)):
            for entry in mr["RaceTable"]["Races"]:
                for lap in entry["Laps"]:
                    for timing in lap["Timings"]:
                        seconds = parse_duration(timing.get("time"))
                        if seconds is not None:
                            laps.setdefault(timing["driverId"], {})[int(lap["number"])] = (
                                seconds, int(timing.get("position") or 0))
        return laps

    def pit_stops(self, race: RaceInfo) -> dict[str, dict[int, float]]:
        """driverId -> {lap: pit lane seconds}."""
        if race.season < FIRST_PIT_STOP_SEASON:
            return {}
        dumped = self._from_dump(race)
        if dumped is not None:
            return dumped.pit_stops
        stops: dict[str, dict[int, float]] = {}
        for mr in self._pages(f"{race.season}/{race.round}/pitstops/", self._ttl(race.season)):
            for entry in mr["RaceTable"]["Races"]:
                for stop in entry.get("PitStops", []):
                    seconds = parse_duration(stop.get("duration")) or 0.0
                    stops.setdefault(stop["driverId"], {})[int(stop["lap"])] = seconds
        return stops

    # ---- OpenF1 -------------------------------------------------------------------------

    def session_data(self, endpoint: str, session_key: int, extra=None) -> list[dict]:
        params = [("session_key", "=", session_key)] + (extra or [])
        return self.openf1.get(endpoint, params)

    def circuit_sessions(self, short_name: str, now: float | None = None) -> list[dict]:
        """Finished race sessions at a circuit, newest first."""
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        rows = self.openf1.get("sessions", [("circuit_short_name", "=", short_name),
                                            ("session_name", "=", "Race")], DAY_S)
        done = [s for s in rows if not s.get("is_cancelled")
                and parse_time(s["date_end"]) + 3600 < now]
        return sorted(done, key=lambda s: s["date_start"], reverse=True)


def match_session(race_date: str, sessions: list[dict]) -> dict | None:
    """Find the OpenF1 race session for a Jolpica race date (UTC dates can differ by a day)."""
    day = datetime.fromisoformat(race_date).date()
    for session in sessions:
        session_day = datetime.fromisoformat(session["date_start"]).date()
        if abs((session_day - day).days) <= 1 and not session.get("is_cancelled"):
            return session
    return None


def classify_race(row: dict, sessions: list[dict], now: float) -> RaceInfo:
    """Decide what a race can offer, and say why when the answer is a limitation."""
    season = int(row["season"])
    start_text = f"{row['date']}T{row.get('time', '12:00:00Z')}"
    start = parse_time(start_text)
    session = match_session(row["date"], sessions)

    if start + RACE_DURATION_MARGIN.total_seconds() > now:
        availability, note = AVAIL_FUTURE, "Race has not finished yet."
    elif session is not None:
        availability, note = AVAIL_TELEMETRY, "OpenF1 location data (approximate positions)."
    elif season >= FIRST_LAP_TIMING_SEASON:
        availability = AVAIL_ESTIMATED
        note = "No location data; positions estimated from lap times."
        if season >= FIRST_TELEMETRY_SEASON:
            note = "OpenF1 has no session; positions estimated from lap times."
    else:
        availability = AVAIL_UNAVAILABLE
        note = f"Results only: no lap timing exists before {FIRST_LAP_TIMING_SEASON}."

    return RaceInfo(season, int(row["round"]), row["raceName"], row["Circuit"]["circuitId"],
                    row["Circuit"]["circuitName"], row["date"], start, session, availability, note)


__all__ = ["Sources", "RaceInfo", "DataUnavailable", "classify_race", "match_session",
           "parse_duration", "parse_time", "iso", "AVAIL_TELEMETRY", "AVAIL_ESTIMATED",
           "AVAIL_UNAVAILABLE", "AVAIL_FUTURE", "FIRST_LAP_TIMING_SEASON",
           "FIRST_PIT_STOP_SEASON", "FIRST_TELEMETRY_SEASON"]
