"""Turn a selected race into files on disk, and keep the manifest up to date."""

from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone
from pathlib import Path

from . import estimate, race_format as rf, telemetry
from .http_client import DataUnavailable
from .outline import best_outline
from .sources import (AVAIL_ESTIMATED, AVAIL_FUTURE, AVAIL_TELEMETRY, AVAIL_UNAVAILABLE, RaceInfo,
                      Sources)

ROOT = "f1replay"
MANIFEST_VERSION = 1
MAX_LINE = 255
LICENCE_NOTE = ("Source data: OpenF1 (openf1.org, CC BY-NC-SA 4.0) and Jolpica F1 "
                "(jolpi.ca, CC BY-NC-SA 4.0). Personal, non-commercial use. Unofficial; not "
                "associated with Formula 1 companies.")


def root_dir(out_dir: Path) -> Path:
    return Path(out_dir) / ROOT


def build_race(src: Sources, season: int, round_number: int, out_dir: Path, log=print) -> dict:
    """Download, convert and write one race. Returns its report."""
    race = src.race(season, round_number)
    log(f"{race.season} round {race.round}: {race.name} ({race.date})")
    if race.availability == AVAIL_FUTURE:
        raise DataUnavailable(f"{race.name} has not finished yet.")
    if race.availability == AVAIL_UNAVAILABLE:
        raise DataUnavailable(f"{race.name}: {race.note}")

    package = None
    fallback_notes: list[str] = []
    if race.availability == AVAIL_TELEMETRY:
        try:
            package = telemetry.build(src, race, log)
        except telemetry.NoTelemetry as problem:
            log(f"  Telemetry unusable: {problem} Falling back to lap-time estimation.")
            fallback_notes.append(f"Telemetry unusable: {problem}")
    if package is None:
        log("  Estimating positions from lap times")
        package = estimate.build(src, race, best_outline(src, race.circuit_id), log,
                                 fallback_notes)

    path = root_dir(out_dir) / "races" / f"{race.stem}.f1r"
    size, crc = rf.write_package(package, path)
    report = make_report(race, package, path, size, crc)
    report_path = root_dir(out_dir) / "reports" / f"{race.stem}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"  Wrote {path.name}: {size / 1e6:.1f} MB, {package.x.shape[0]} frames, "
        f"{len(package.drivers)} cars, "
        f"{'telemetry' if package.fidelity == rf.FIDELITY_TELEMETRY else 'ESTIMATED'}")
    for note in package.notes:
        log(f"  Note: {note}")
    return report


def make_report(race: RaceInfo, package: rf.RacePackage, path: Path, size: int, crc: int) -> dict:
    return {
        "season": race.season, "round": race.round, "race": race.name,
        "circuit": race.circuit_name, "date": race.date,
        "fidelity": "telemetry" if package.fidelity == rf.FIDELITY_TELEMETRY else "estimated",
        "flags": package.flags, "file": path.name, "size": size, "crc32": f"{crc:08x}",
        "format_version": rf.VERSION, "sources": package.sources,
        "outline_source": package.outline_source, "notes": package.notes,
        "licence": LICENCE_NOTE,
        "first_frame_utc_ms": package.start_epoch_ms, "lights_out_ms": package.lights_out_ms,
        "openf1_session_key": race.session["session_key"] if race.session else None,
        "drivers": [{"number": d.number, "code": d.code, "name": d.name, "team": d.team,
                     "colour": "%02X%02X%02X" % d.rgb, "grid": d.grid,
                     "final_position": d.final_position} for d in package.drivers],
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def catalog_path(out_dir: Path) -> Path:
    return Path(out_dir) / "catalog.json"


def load_catalog(out_dir: Path) -> dict[str, list[dict]]:
    path = catalog_path(out_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def add_to_catalog(src: Sources, seasons: list[int], out_dir: Path, log=print) -> None:
    catalog = load_catalog(out_dir)
    for season in seasons:
        races = src.races(season)
        catalog[str(season)] = [{"season": r.season, "round": r.round, "name": r.name,
                                 "circuit": r.circuit_name, "date": r.date,
                                 "availability": r.availability, "note": r.note} for r in races]
        log(f"  {season}: {len(races)} races")
    catalog_path(out_dir).parent.mkdir(parents=True, exist_ok=True)
    catalog_path(out_dir).write_text(json.dumps(catalog, indent=1, ensure_ascii=False),
                                     encoding="utf-8")


def _short_note(report: dict) -> str:
    """One line for the race list, naming the most important limitation."""
    flags = report["flags"]
    text = ("Approximate positions (OpenF1)" if report["fidelity"] == "telemetry"
            else "ESTIMATED from lap times")
    if flags & rf.FLAG_SCHEMATIC:
        text += "; schematic track"
    if flags & rf.FLAG_LAYOUT_DIFFERS:
        text += "; layout differs"
    if flags & rf.FLAG_MIXED_ESTIMATED:
        text += "; some cars estimated"
    if flags & rf.FLAG_INCOMPLETE:
        text += "; incomplete data"
    return text


def _clean(text: str) -> str:
    return rf.ascii_text(str(text)).replace("\t", " ")


def manifest_rows(out_dir: Path) -> list[list[str]]:
    rows: dict[tuple[int, int], list[str]] = {}
    for races in load_catalog(out_dir).values():
        for race in races:
            status = {AVAIL_TELEMETRY: "N", AVAIL_ESTIMATED: "N", AVAIL_UNAVAILABLE: "U",
                      AVAIL_FUTURE: "F"}[race["availability"]]
            fidelity = race["availability"] if status == "N" else "-"
            note = race["note"]
            if status == "N":
                note = f"Not on card yet. {note}"
            rows[(race["season"], race["round"])] = [
                str(race["season"]), str(race["round"]), status, fidelity, "-", "0", "0",
                _clean(race["name"]), _clean(race["circuit"]), race["date"], _clean(note)]

    for report_path in sorted((root_dir(out_dir) / "reports").glob("*.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        race_file = root_dir(out_dir) / "races" / report["file"]
        if not race_file.exists() or race_file.stat().st_size != report["size"]:
            continue
        rows[(report["season"], report["round"])] = [
            str(report["season"]), str(report["round"]), "R",
            "T" if report["fidelity"] == "telemetry" else "E", report["file"],
            str(report["size"]), report["crc32"], _clean(report["race"]),
            _clean(report["circuit"]), report["date"], _clean(_short_note(report))]
    return [rows[key] for key in sorted(rows)]


def fit_line(row: list[str]) -> str:
    """Join a manifest row, shortening text fields so it fits the firmware's line buffer."""
    row = list(row)
    limits = {10: 110, 8: 44, 7: 44}
    for index, limit in limits.items():
        row[index] = row[index][:limit]
    return "\t".join(row)[:MAX_LINE]


def write_manifest(out_dir: Path) -> Path:
    path = root_dir(out_dir) / "manifest.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"F1RM\t{MANIFEST_VERSION}\t{stamp}"] + [fit_line(r) for r in manifest_rows(out_dir)]
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return path


def verify_all(out_dir: Path, log=print) -> int:
    """Check every race file against the format and its report. Returns the failure count."""
    failures = 0
    files = sorted((root_dir(out_dir) / "races").glob("*.f1r"))
    if not files:
        log("No race files found. Build one first.")
    for path in files:
        try:
            data = rf.read_package(path)
            report_path = root_dir(out_dir) / "reports" / f"{path.stem}.json"
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if report["crc32"] != f"{zlib.crc32(path.read_bytes()):08x}":
                    raise rf.BadRaceFile("checksum differs from its report")
            kind = "telemetry" if data["fidelity"] == rf.FIDELITY_TELEMETRY else "estimated"
            log(f"OK   {path.name}  {data['strings'][1][0]}  {kind}, {len(data['drivers'])} cars, "
                f"{data['n_frames']} frames")
        except rf.BadRaceFile as problem:
            failures += 1
            log(f"BAD  {path.name}: {problem}")
    return failures
