#!/usr/bin/env python3
"""F1 replay data tool. Run with no arguments for a simple menu."""

from __future__ import annotations

import argparse
import getpass
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from f1data import builder, preview, server  # noqa: E402
from f1data.http_client import DataUnavailable, NetworkError  # noqa: E402
from f1data.race_format import BadRaceFile  # noqa: E402
from f1data.sources import (AVAIL_ESTIMATED, AVAIL_FUTURE, AVAIL_TELEMETRY,  # noqa: E402
                            AVAIL_UNAVAILABLE, Sources)

CACHE_DIR = HERE / "cache"
OUT_DIR = HERE / "out"
DEFAULT_PORT = 8765
LABELS = {AVAIL_TELEMETRY: "telemetry", AVAIL_ESTIMATED: "estimated",
          AVAIL_UNAVAILABLE: "unavailable", AVAIL_FUTURE: "not raced yet"}


def sources() -> Sources:
    return Sources(CACHE_DIR)


def parse_seasons(text: str) -> list[int]:
    """'2023', '2021-2024' or '2019,2021'."""
    years: list[int] = []
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        years += range(int(first), int(last or first) + 1)
    return years


def cmd_seasons(_args) -> int:
    years = sources().seasons()
    print(f"Seasons known to Jolpica: {years[0]}-{years[-1]}")
    print("  1950-1995  results only, so no replay can be built")
    print("  1996-2022  ESTIMATED replays from real lap times")
    print("  2023 on    approximate positions from OpenF1 location data")
    return 0


def cmd_list(args) -> int:
    races = sources().races(args.season)
    if not races:
        print(f"No races found for {args.season}.")
        return 1
    print(f"{'Rd':>2}  {'Date':10}  {'Race':32}  {'Replay':13}  Notes")
    for race in races:
        built = (builder.root_dir(OUT_DIR) / "races" / f"{race.stem}.f1r").exists()
        print(f"{race.round:>2}  {race.date}  {race.name[:32]:32}  "
              f"{LABELS[race.availability]:13}  {'[built] ' if built else ''}{race.note}")
    return 0


def _build(src: Sources, season: int, rounds: list[int]) -> int:
    failures = 0
    for round_number in rounds:
        try:
            builder.build_race(src, season, round_number, OUT_DIR)
        except DataUnavailable as problem:
            failures += 1
            print(f"  UNAVAILABLE: {problem}")
    builder.add_to_catalog(src, [season], OUT_DIR, log=lambda _text: None)
    print(f"Manifest updated: {builder.write_manifest(OUT_DIR)}")
    print(f"Network requests this run: {src.network_calls()} (everything else came from cache)")
    return 1 if failures else 0


def cmd_build(args) -> int:
    src = sources()
    if args.all:
        rounds = [r.round for r in src.races(args.season)
                  if r.availability in (AVAIL_TELEMETRY, AVAIL_ESTIMATED)]
    elif args.round:
        rounds = [args.round]
    else:
        print("Give --round N or --all.")
        return 2
    return _build(src, args.season, rounds)


def cmd_catalog(args) -> int:
    src = sources()
    print("Adding seasons to the catalog:")
    builder.add_to_catalog(src, parse_seasons(args.seasons), OUT_DIR)
    print(f"Manifest updated: {builder.write_manifest(OUT_DIR)}")
    return 0


def cmd_verify(_args) -> int:
    failures = builder.verify_all(OUT_DIR)
    print("All race files are valid." if not failures else f"{failures} bad file(s).")
    return 1 if failures else 0


def cmd_deploy(args) -> int:
    """Copy new or changed files to the card. Never deletes anything on it."""
    drive = args.drive.rstrip("\\/")
    if len(drive) == 1:
        drive += ":"
    target = Path(drive + "/") / builder.ROOT
    if not Path(drive + "/").exists():
        print(f"Drive {drive} was not found. Is the microSD card inserted?")
        return 1
    source = builder.root_dir(OUT_DIR)
    if not (source / "manifest.txt").exists():
        print("Nothing to copy yet. Build a race first.")
        return 1
    if builder.verify_all(OUT_DIR, log=lambda _text: None):
        print("A race file failed verification, so nothing was copied. Run: f1tool.py verify")
        return 1
    files = [p for p in source.rglob("*") if p.is_file() and p.name != "wifi.txt"]
    wifi = source / "wifi.txt"
    if wifi.exists():
        files.append(wifi)
    pending = []
    for path in files:
        destination = target / path.relative_to(source)
        same = (destination.exists() and destination.stat().st_size == path.stat().st_size
                and path.suffix == ".f1r")
        if not same:
            pending.append((path, destination))
    need = sum(p.stat().st_size for p, _ in pending)
    free = shutil.disk_usage(drive + "/").free
    if need > free:
        print(f"The card needs {need / 1e6:.0f} MB but has {free / 1e6:.0f} MB free.")
        return 1
    for path, destination in pending:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        print(f"  copied {path.relative_to(source)}")
    print(f"Done: {len(pending)} file(s) copied to {target}. Eject the card safely before "
          "removing it.")
    return 0


def cmd_serve(args) -> int:
    if not (builder.root_dir(OUT_DIR) / "manifest.txt").exists():
        print("Nothing to serve yet. Build a race first.")
        return 1
    server.serve(OUT_DIR, args.port)
    return 0


def cmd_wifi(args) -> int:
    """Write wifi.txt. The password is typed here so it stays out of shell history."""
    ssid = args.ssid or input("Wi-Fi network name (2.4 GHz only): ").strip()
    password = getpass.getpass("Wi-Fi password (hidden as you type): ")
    url = args.server or f"http://{server.local_ip()}:{DEFAULT_PORT}"
    path = builder.root_dir(OUT_DIR) / "wifi.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"ssid={ssid}\npassword={password}\nserver={url}\n", encoding="utf-8")
    print(f"Wrote {path}. It goes to the card with deploy and is never served or committed.")
    return 0


def cmd_preview(args) -> int:
    path = builder.root_dir(OUT_DIR) / "races" / f"{args.season}_{args.round:02d}.f1r"
    if not path.exists():
        print(f"{path.name} has not been built yet.")
        return 1
    svg = OUT_DIR / f"{path.stem}_preview.svg"
    preview.write_svg(path, svg, args.at)
    print(f"Wrote {svg}. Open it in a web browser.")
    return 0


def menu() -> int:
    print("F1 Replay data tool\n")
    while True:
        print("  1  List races in a season")
        print("  2  Download and build one race")
        print("  3  Add seasons to the on-device catalog")
        print("  4  Check all built race files")
        print("  5  Copy to the microSD card")
        print("  6  Serve races over Wi-Fi")
        print("  Q  Quit")
        choice = input("\nChoose: ").strip().lower()
        try:
            if choice == "1":
                cmd_list(argparse.Namespace(season=int(input("Season: "))))
            elif choice == "2":
                season = int(input("Season: "))
                cmd_list(argparse.Namespace(season=season))
                _build(sources(), season, [int(input("Round: "))])
            elif choice == "3":
                cmd_catalog(argparse.Namespace(seasons=input("Seasons, e.g. 2021-2024: ")))
            elif choice == "4":
                cmd_verify(None)
            elif choice == "5":
                cmd_deploy(argparse.Namespace(drive=input("Card drive letter, e.g. E: ")))
            elif choice == "6":
                cmd_serve(argparse.Namespace(port=DEFAULT_PORT))
            elif choice == "q":
                return 0
        except ValueError:
            print("Please enter a number.")
        except (DataUnavailable, NetworkError) as problem:
            print(f"Problem: {problem}")
        print()


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description=__doc__)
    commands = main.add_subparsers(dest="command")

    commands.add_parser("seasons", help="show available seasons").set_defaults(run=cmd_seasons)

    item = commands.add_parser("list", help="list a season's races and their data level")
    item.add_argument("--season", type=int, required=True)
    item.set_defaults(run=cmd_list)

    item = commands.add_parser("build", help="download and convert races")
    item.add_argument("--season", type=int, required=True)
    item.add_argument("--round", type=int)
    item.add_argument("--all", action="store_true", help="every available race in the season")
    item.set_defaults(run=cmd_build)

    item = commands.add_parser("catalog", help="list whole seasons on the device")
    item.add_argument("--seasons", required=True, help="e.g. 2023 or 2021-2024")
    item.set_defaults(run=cmd_catalog)

    commands.add_parser("verify", help="check built files").set_defaults(run=cmd_verify)

    item = commands.add_parser("deploy", help="copy files to the microSD card")
    item.add_argument("--drive", required=True, help="e.g. E:")
    item.set_defaults(run=cmd_deploy)

    item = commands.add_parser("serve", help="serve races to the ESP32 over Wi-Fi")
    item.add_argument("--port", type=int, default=DEFAULT_PORT)
    item.set_defaults(run=cmd_serve)

    item = commands.add_parser("wifi", help="create wifi.txt for Wi-Fi sync")
    item.add_argument("--ssid")
    item.add_argument("--server", help="default: this PC's address")
    item.set_defaults(run=cmd_wifi)

    item = commands.add_parser("preview", help="draw a moment of a race as an SVG")
    item.add_argument("--season", type=int, required=True)
    item.add_argument("--round", type=int, required=True)
    item.add_argument("--at", type=float, default=0.5, help="0 = start, 1 = end")
    item.set_defaults(run=cmd_preview)
    return main


def main() -> int:
    args = parser().parse_args()
    if not args.command:
        return menu()
    try:
        return args.run(args)
    except (DataUnavailable, NetworkError, BadRaceFile) as problem:
        print(f"Problem: {problem}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
