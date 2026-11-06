"""Build a race package from OpenF1 location samples (2023 onwards)."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from . import race_format as rf
from .estimate import (DriverTiming, FRAME_DT_S, PRE_ROLL_S, build_motion, grid_offset, hex_rgb,
                       median_green_lap, progress_at)
from .http_client import DataUnavailable
from .outline import (LOCATION_UNITS_PER_M, Outline, clean_lap_candidates, fit_transform,
                      geojson_outline, outline_from_samples, schematic_outline)
from .sources import RaceInfo, Sources, iso, parse_time

POST_ROLL_S = 10.0
MAX_SAMPLE_GAP_S = 3.0
MIN_DRIVER_COVERAGE = 0.5     # Below this a car's dot falls back to lap-timing estimates.
MIN_RACE_COVERAGE = 0.5       # Below this share of usable cars, the race is not "telemetry".
GAP_MAX_AGE_S = 30.0
PARKED_RADIUS_M = 30.0
DEFAULT_PIT_LANE_S = 25.0
CAR_BOUNDS_PERCENTILES = (0.1, 99.9)
LAP_TIME_MIN_S = 30.0
MAX_CAR_SPEED_MS = 150.0      # Generous: sample timestamps are noisy over short steps.
SPEED_SLACK_M = 60.0
PIT_WINDOW_FALLBACK_S = 12.0


class NoTelemetry(Exception):
    """OpenF1 lacks enough location data for this race."""


@dataclass
class Track:
    """One car's location samples, relative to lights-out."""

    t: np.ndarray
    x: np.ndarray
    y: np.ndarray


def remove_glitches(t: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Return a mask of plausible samples, dropping impossible jumps in the location feed.

    Each sample is compared with the last one kept, so the allowance grows with
    elapsed time and a real long gap can never reject the rest of the race.
    """
    keep = np.ones(len(t), bool)
    last = 0
    limit_m = MAX_CAR_SPEED_MS * LOCATION_UNITS_PER_M
    slack = SPEED_SLACK_M * LOCATION_UNITS_PER_M
    for i in range(1, len(t)):
        distance = math.hypot(x[i] - x[last], y[i] - y[last])
        if distance > limit_m * (t[i] - t[last]) + slack:
            keep[i] = False
        else:
            last = i
    return keep


def pit_windows(pit_rows: list[dict], history: list[tuple], t0: float,
                out_lap_numbers: set[int]) -> tuple[list[tuple[float, float]], set[int]]:
    """Return (pit lane time windows, in-lap numbers) for one driver.

    OpenF1's pit ``date`` is when the car leaves the pit lane, which was confirmed
    against location samples: the car stops about 17 s before it. Some 2023 races
    have no pit rows, so out-laps give an approximate window around the timing line.
    """
    if pit_rows:
        windows = []
        for row in pit_rows:
            leave = parse_time(row["date"]) - t0
            lane = row.get("lane_duration") or row.get("pit_duration") or DEFAULT_PIT_LANE_S
            windows.append((leave - lane, leave))
        return windows, {row.get("lap_number") for row in pit_rows}
    starts = {lap: start for lap, start, _, _ in history}
    windows = [(starts[lap] - PIT_WINDOW_FALLBACK_S, starts[lap] + PIT_WINDOW_FALLBACK_S)
               for lap in out_lap_numbers if lap in starts and lap > 1]
    return windows, {lap - 1 for lap in out_lap_numbers}


def lights_out_time(laps: list[dict], race_control: list[dict], session: dict) -> float:
    """Lap 1 starts at lights-out, so the most common lap 1 start time is the race start."""
    starts = [lap["date_start"] for lap in laps if lap.get("lap_number") == 1
              and lap.get("date_start")]
    if starts:
        return parse_time(Counter(starts).most_common(1)[0][0])
    scheduled = parse_time(session["date_start"])
    for message in sorted(race_control, key=lambda m: m["date"]):
        moment = parse_time(message["date"])
        if message.get("message") == "SESSION STARTED" and moment >= scheduled - 3600:
            return moment
    return scheduled


def track_status_events(race_control: list[dict], t0: float) -> list[tuple[float, int]]:
    """Reduce race control messages to (time, status) changes."""
    events = []
    current = rf.TRACK_GREEN
    for message in sorted(race_control, key=lambda m: m["date"]):
        text = (message.get("message") or "").upper()
        flag = message.get("flag")
        scope = message.get("scope")
        new = current
        if message.get("category") == "SafetyCar":
            if "VIRTUAL" in text or "VSC" in text:
                new = rf.TRACK_VSC if "DEPLOYED" in text else (
                    rf.TRACK_GREEN if "ENDING" in text else current)
            elif "DEPLOYED" in text:
                new = rf.TRACK_SC
        elif flag == "RED":
            new = rf.TRACK_RED
        elif flag in ("GREEN", "CLEAR") and scope == "Track":
            new = rf.TRACK_GREEN
        elif flag == "YELLOW" and scope == "Track":
            new = rf.TRACK_YELLOW
        if new != current:
            events.append((parse_time(message["date"]) - t0, new))
            current = new
    return events


def apply_status(events: list[tuple[float, int]], times: np.ndarray) -> np.ndarray:
    status = np.zeros(len(times), np.uint8)
    for moment, value in events:
        status[times >= moment] = value
    return status


def step_values(rows_t: np.ndarray, rows_v: np.ndarray, times: np.ndarray, default: int) -> np.ndarray:
    """Latest value at or before each time; ``default`` before the first row."""
    if len(rows_t) == 0:
        return np.full(len(times), default, dtype=np.int64)
    index = np.searchsorted(rows_t, times, side="right") - 1
    return np.where(index >= 0, rows_v[np.maximum(index, 0)], default)


def encode_gap(value) -> int:
    """OpenF1 gap_to_leader: seconds, '+1 LAP', or None."""
    if value is None:
        return rf.GAP_UNKNOWN
    if isinstance(value, (int, float)):
        return int(min(max(value, 0) * 10, rf.GAP_LAPS_BASE - 1))
    match = re.match(r"\+?(\d+)\s*LAP", str(value).upper())
    return rf.GAP_LAPS_BASE + min(int(match.group(1)), 254) if match else rf.GAP_UNKNOWN


def dense_order(position: np.ndarray) -> np.ndarray:
    """Make positions unique per frame (the live feed can briefly show ties)."""
    order = np.argsort(position, axis=1, kind="stable")
    result = np.empty_like(position)
    rows = np.arange(position.shape[0])[:, None]
    result[rows, order] = np.arange(1, position.shape[1] + 1)
    return result


def last_moving_time(track: Track) -> float:
    """When a retired car stopped: the last time it was outside a small parking radius."""
    if len(track.t) == 0:
        return 0.0
    end_x, end_y = track.x[-1], track.y[-1]
    away = np.hypot(track.x - end_x, track.y - end_y) > PARKED_RADIUS_M * LOCATION_UNITS_PER_M
    return float(track.t[np.nonzero(away)[0][-1]]) if away.any() else float(track.t[0])


def load_laps(rows: list[dict], t0: float) -> tuple[list[tuple[int, float, float, bool]], int]:
    """Return ([(lap, start_s, end_s, derived)], laps_with_unknown_end) for one driver.

    Lap duration can be null. When it is, the end comes from the next lap's start.
    """
    ordered = sorted((r for r in rows if r.get("lap_number")), key=lambda r: r["lap_number"])
    laps = []
    unknown = 0
    previous_end = 0.0
    for index, row in enumerate(ordered):
        start = parse_time(row["date_start"]) - t0 if row.get("date_start") else previous_end
        duration = row.get("lap_duration")
        derived = False
        if duration:
            end = start + duration
        elif index + 1 < len(ordered) and ordered[index + 1].get("date_start"):
            end = parse_time(ordered[index + 1]["date_start"]) - t0
            derived = True
        else:
            unknown += 1
            continue
        laps.append((row["lap_number"], start, end, derived))
        previous_end = end
    return laps, unknown


def missing_lap_rows(history: list[tuple]) -> int:
    """Count laps the source skipped entirely, between a car's first and last recorded laps."""
    if not history:
        return 0
    numbers = {lap for lap, _, _, _ in history}
    return history[-1][0] - len(numbers)


def finish_time(history: list[tuple], result: dict) -> float:
    """When a classified car took the flag.

    Normally the end of its last recorded lap. If the source has no end for the final
    lap, the official race duration is used, so the car is not marked finished a lap early.
    """
    last_end = history[-1][2] if history else 0.0
    official = result.get("duration")
    laps_recorded = history[-1][0] if history else 0
    if isinstance(official, (int, float)) and laps_recorded < (result.get("number_of_laps") or 0):
        return max(last_end, float(official))
    return last_end


def choose_outline(src: Sources, race: RaceInfo, laps: list[dict],
                   tracks: dict[int, Track], t0: float) -> Outline:
    """Use this race's own quickest clean lap, falling back if its samples are unusable."""
    for lap in clean_lap_candidates(laps):
        track = tracks.get(lap["driver_number"])
        if track is None:
            continue
        start = parse_time(lap["date_start"]) - t0
        source = f"OpenF1 location samples, {race.season} race, lap {lap['lap_number']}"
        outline = outline_from_samples(track.t, track.x, track.y, start, lap["lap_duration"],
                                       source)
        if outline is not None:
            return outline
    return geojson_outline(src, race.circuit_id) or schematic_outline()


def _to_metres(track: Track) -> np.ndarray:
    return np.column_stack([track.x, track.y]) / LOCATION_UNITS_PER_M


def build(src: Sources, race: RaceInfo, log=print) -> rf.RacePackage:
    session = race.session
    key = session["session_key"]
    log(f"  OpenF1 session {key}: downloading timing data")
    entries = src.session_data("drivers", key)
    laps = src.session_data("laps", key)
    race_control = src.session_data("race_control", key)
    if not entries or not laps:
        raise NoTelemetry("OpenF1 has no driver or lap data for this session.")
    position_rows = src.session_data("position", key)
    interval_rows = src.session_data("intervals", key)
    pit_rows = src.session_data("pit", key)
    result_rows = src.session_data("session_result", key)

    t0 = lights_out_time(laps, race_control, session)
    laps_by_driver: dict[int, list[dict]] = {}
    for lap in laps:
        laps_by_driver.setdefault(lap["driver_number"], []).append(lap)
    results = {r["driver_number"]: r for r in result_rows}

    lap_history = {}
    unknown_ends = 0
    skipped_laps = 0
    race_end = 0.0
    for number, rows in laps_by_driver.items():
        lap_history[number], unknown = load_laps(rows, t0)
        unknown_ends += unknown
        skipped_laps += missing_lap_rows(lap_history[number])
        if lap_history[number]:
            race_end = max(race_end, finish_time(lap_history[number], results.get(number, {})))
    if race_end < LAP_TIME_MIN_S:
        raise NoTelemetry("OpenF1 lap data for this session is too sparse to time the race.")

    begin, finish = t0 - PRE_ROLL_S - 2, t0 + race_end + POST_ROLL_S + 2
    tracks: dict[int, Track] = {}
    glitches = 0
    entries = sorted(entries, key=lambda e: e["driver_number"])
    for count, entry in enumerate(entries, start=1):
        number = entry["driver_number"]
        log(f"  location {count}/{len(entries)}: {entry.get('name_acronym', number)}")
        rows = src.session_data("location", key, [("driver_number", "=", number),
                                                  ("date", ">=", iso(begin)),
                                                  ("date", "<=", iso(finish))])
        rows = [r for r in rows if r.get("x") or r.get("y")]  # 0,0 means no fix.
        if rows:
            t = np.array([parse_time(r["date"]) - t0 for r in rows])
            x = np.array([r["x"] for r in rows], float)
            y = np.array([r["y"] for r in rows], float)
            keep = remove_glitches(t, x, y)
            glitches += int((~keep).sum())
            tracks[number] = Track(t[keep], x[keep], y[keep])
    if not tracks:
        raise NoTelemetry("OpenF1 has no location samples for this session.")

    n_frames = int(math.ceil((PRE_ROLL_S + race_end + POST_ROLL_S) / FRAME_DT_S)) + 1
    times = np.arange(n_frames) * FRAME_DT_S - PRE_ROLL_S
    outline = choose_outline(src, race, laps, tracks, t0)

    # Who raced, and from where. Jolpica supplies the official classification.
    official: dict[int, dict] = {}
    try:
        for row in src.results(race):
            if row.get("number"):
                official[int(row["number"])] = row
    except DataUnavailable:
        pass

    notes = ["APPROXIMATE positions from OpenF1 location data (about 3.7 samples per second). "
             "Lateral track position is not reliable."]
    flags = 0
    drivers: list[rf.Driver] = []
    numbers: list[int] = []
    teams_seen: set[str] = set()
    missing_location: list[str] = []
    no_start: list[str] = []
    entries.sort(key=lambda e: (results.get(e["driver_number"], {}).get("position") or 99,
                                e["driver_number"]))
    for entry in entries:
        number = entry["driver_number"]
        result = results.get(number, {})
        code = entry.get("name_acronym") or str(number)
        if result.get("dns") or (not lap_history.get(number) and number not in tracks):
            no_start.append(code)
            continue
        off = official.get(number, {})
        team = entry.get("team_name") or off.get("Constructor", {}).get("name") or "Unknown"
        classified = not (result.get("dnf") or result.get("dsq"))
        final = int(off.get("position") or result.get("position") or 0) if classified else 0
        drivers.append(rf.Driver(number, code, entry.get("full_name") or code, team,
                                 hex_rgb(entry.get("team_colour")),
                                 int(off.get("grid") or 0), final, team in teams_seen))
        teams_seen.add(team)
        numbers.append(number)
    if no_start:
        notes.append("Did not start or no data: " + ", ".join(no_start) + ".")

    n = len(drivers)
    x = np.zeros((n_frames, n))
    y = np.zeros((n_frames, n))
    state = np.zeros((n_frames, n), np.uint8)
    no_data = np.zeros((n_frames, n), bool)
    estimated = np.zeros((n_frames, n), bool)
    lap = np.zeros((n_frames, n), np.int32)
    position = np.zeros((n_frames, n), np.int64)
    gap = np.full((n_frames, n), rf.GAP_UNKNOWN, np.uint16)
    laps_table: list[rf.LapRecord] = []
    pits: dict[int, list[dict]] = {}
    for row in pit_rows:
        pits.setdefault(row["driver_number"], []).append(row)

    # The telemetry map has to contain the pit lane as well as the racing line.
    samples = [_to_metres(track) for track in tracks.values()]
    every_sample = np.vstack(samples)
    low = np.percentile(every_sample, CAR_BOUNDS_PERCENTILES[0], axis=0)
    high = np.percentile(every_sample, CAR_BOUNDS_PERCENTILES[1], axis=0)
    within = every_sample[np.all((every_sample >= low) & (every_sample <= high), axis=1)]
    bounds = np.vstack([outline.points, within[::50]]) if outline.telemetry_based else outline.points
    transform = fit_transform(bounds)

    timings_for_median = [DriverTiming([e - s for _, s, e, _ in lap_history.get(num, [])])
                          for num in numbers]
    median_lap = median_green_lap(timings_for_median)
    usable = 0
    fallback_cars: list[str] = []

    for i, number in enumerate(numbers):
        history = lap_history.get(number, [])
        result = results.get(number, {})
        retired = bool(result.get("dnf"))
        last_lap_end = history[-1][2] if history else 0.0

        track = tracks.get(number)
        active = times <= (last_lap_end if history else race_end)
        coverage = 0.0
        if track is not None and len(track.t) > 1:
            index = np.searchsorted(track.t, times, side="right")
            before = np.clip(index - 1, 0, len(track.t) - 1)
            after = np.clip(index, 0, len(track.t) - 1)
            nearest_gap = track.t[after] - track.t[before]
            missing = (times < track.t[0]) | (times > track.t[-1]) | (nearest_gap > MAX_SAMPLE_GAP_S)
            coverage = 1.0 - float(missing[active].mean()) if active.any() else 0.0

        if track is not None and coverage >= MIN_DRIVER_COVERAGE:
            usable += 1
            pts = transform.apply(_to_metres(track))
            x[:, i] = np.interp(times, track.t, pts[:, 0])
            y[:, i] = np.interp(times, track.t, pts[:, 1])
            # Hold the last known position through a gap, and flag it, rather than invent one.
            hold = np.where(missing)[0]
            x[hold, i] = pts[before[hold], 0]
            y[hold, i] = pts[before[hold], 1]
            no_data[:, i] = missing
        elif history:
            fallback_cars.append(drivers[i].code)
            timing = DriverTiming([e - s for _, s, e, _ in history], {}, drivers[i].grid)
            start = -grid_offset(timing.grid, n, outline.length_m)
            progress = progress_at(build_motion(timing, start, median_lap), times)
            mapped = transform.apply(outline.fraction_to_xy(progress))
            x[:, i], y[:, i] = mapped[:, 0], mapped[:, 1]
            estimated[:, i] = True
        else:
            no_data[:, i] = True

        out_laps = {r["lap_number"] for r in laps_by_driver.get(number, [])
                    if r.get("is_pit_out_lap")}
        windows, pit_laps = pit_windows(pits.get(number, []), history, t0, out_laps)
        for enter, leave in windows:
            state[(times >= enter) & (times < leave), i] = rf.STATE_PIT

        if retired:
            stop = last_lap_end
            if track is not None and coverage >= MIN_DRIVER_COVERAGE:
                stop = max(last_lap_end, last_moving_time(track))
            state[times >= stop, i] = rf.STATE_OUT
        elif history:
            state[times >= finish_time(history, result), i] = rf.STATE_FINISHED

        if history:
            starts = np.array([s for _, s, _, _ in history])
            numbers_of_laps = np.array([num for num, _, _, _ in history])
            lap[:, i] = step_values(starts, numbers_of_laps, times, 0)
            lap[times < 0, i] = 0

        for lap_number, start, end, derived in history:
            flag = rf.LAP_PIT_IN if lap_number in pit_laps else 0
            flag |= rf.LAP_PIT_OUT if lap_number - 1 in pit_laps else 0
            flag |= rf.LAP_FILLED if derived else 0
            laps_table.append(rf.LapRecord(i, lap_number, flag, int(end * 1000),
                                           int((end - start) * 1000)))

        rows = sorted((r for r in position_rows if r["driver_number"] == number),
                      key=lambda r: r["date"])
        row_t = np.array([parse_time(r["date"]) - t0 for r in rows])
        row_p = np.array([r["position"] for r in rows])
        position[:, i] = step_values(row_t, row_p, times, drivers[i].grid or 99)

        rows = sorted((r for r in interval_rows if r["driver_number"] == number),
                      key=lambda r: r["date"])
        if rows:
            row_t = np.array([parse_time(r["date"]) - t0 for r in rows])
            row_g = np.array([encode_gap(r.get("gap_to_leader")) for r in rows])
            gap[:, i] = step_values(row_t, row_g, times, rf.GAP_UNKNOWN)
            age = times - step_values(row_t, row_t, times, -10 ** 6)
            gap[age > GAP_MAX_AGE_S, i] = rf.GAP_UNKNOWN

    if usable < MIN_RACE_COVERAGE * n:
        raise NoTelemetry(f"Only {usable} of {n} cars have usable location data.")
    if fallback_cars:
        flags |= rf.FLAG_MIXED_ESTIMATED
        notes.append("Location data missing for " + ", ".join(fallback_cars)
                     + ": those dots are ESTIMATED from lap times.")
    gap_share = float(no_data[state == rf.STATE_RUNNING].mean()) if (state == 0).any() else 0.0
    if gap_share > 0.001:
        notes.append(f"Location data has gaps ({gap_share:.1%} of car-time): hollow dots hold the "
                     "last known position.")
    if gap_share > 0.1:
        flags |= rf.FLAG_INCOMPLETE
    if glitches:
        notes.append(f"{glitches} impossible location samples were discarded.")
    if not pit_rows:
        notes.append("OpenF1 has no pit data for this race: pit times are approximate, from "
                     "out-lap flags.")
    if not interval_rows:
        notes.append("OpenF1 has no interval data for this race: gaps are not shown.")
    if not position_rows:
        flags |= rf.FLAG_INCOMPLETE
        notes.append("OpenF1 has no position data: the running order is by grid position.")
    if unknown_ends:
        notes.append(f"{unknown_ends} lap(s) have no recorded end time, usually a retirement lap "
                     "or the lap back to the pits.")
    if skipped_laps:
        notes.append(f"{skipped_laps} lap record(s) are missing from OpenF1. The dots are "
                     "unaffected, but those lap times are not shown.")
    if not outline.telemetry_based:
        notes.append(f"Track outline: {outline.source}.")
    if outline.schematic:
        flags |= rf.FLAG_SCHEMATIC

    order = dense_order(position)
    leader_index = np.argmin(order, axis=1)
    leader_lap = lap[np.arange(n_frames), leader_index].astype(np.uint8)
    status = apply_status(track_status_events(race_control, t0), times)
    total_laps = max((h[-1][0] for h in lap_history.values() if h), default=0)
    laps_table.sort(key=lambda r: (r.driver_index, r.lap))

    return rf.RacePackage(
        season=race.season, round=race.round, fidelity=rf.FIDELITY_TELEMETRY,
        total_laps=total_laps, race_name=race.name, circuit=race.circuit_name, date=race.date,
        sources="OpenF1 (location, timing, race control); Jolpica F1 (classification)",
        outline_source=outline.source, start_epoch_ms=int((t0 - PRE_ROLL_S) * 1000),
        lights_out_ms=int(PRE_ROLL_S * 1000), map_w=transform.map_w, map_h=transform.map_h,
        outline=transform.apply(outline.points), drivers=drivers, laps=laps_table, x=x, y=y,
        state=state, no_data=no_data, estimated=estimated, position=order.astype(np.uint8),
        lap=lap, gap=gap, track_status=status, leader_lap=leader_lap, flags=flags, notes=notes)
