"""Estimated movement for races that have lap timing but no location data.

Everything here starts from real recorded lap times. What is *estimated* is where
a car was between two timing-line crossings. Jolpica has no sector times, so
within a lap the car follows the circuit's reference speed profile when one is
available, and a constant speed when it is not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import race_format as rf
from .circuits import DEFAULT_COLOUR, TEAM_COLOURS, layout_warning
from .http_client import DataUnavailable
from .outline import Outline, fit_transform
from .sources import FIRST_PIT_STOP_SEASON, RaceInfo, Sources

FRAME_DT_S = 0.5
PRE_ROLL_S = 5.0
POST_ROLL_S = 10.0
GRID_SLOT_M = 8.0
SLOW_LAP_RATIO = 1.25    # A lap this much over the median counts as slow ...
SLOW_FIELD_SHARE = 0.6   # ... and this share of the field makes it a track-wide event.
STALL_RATIO = 1.8        # Over this, the car is treated as stopped (red flag, long repair).
MAX_PIT_HOLD_S = 90.0
DEFAULT_PIT_HOLD_S = 22.0
FINISHED_STATUS = ("Finished", "Lapped")
FILL_WARNING_SHARE = 0.02


@dataclass
class DriverTiming:
    """One car's lap history, in the shape the motion model needs."""

    durations: list[float]
    pit_laps: dict[int, float] = field(default_factory=dict)
    grid: int = 0
    classified: bool = True

    @property
    def lap_ends(self) -> np.ndarray:
        return np.cumsum(self.durations) if self.durations else np.zeros(0)


@dataclass
class Motion:
    """Piecewise-linear lap progress: lap N complete means progress == N."""

    knot_t: np.ndarray
    knot_p: np.ndarray
    pit_windows: list[tuple[float, float]]


def grid_offset(grid: int, field_size: int, length_m: float) -> float:
    """Distance behind the line at the start, as a fraction of a lap."""
    slot = grid if grid > 0 else field_size + 1  # Pit lane starters go to the back.
    return slot * GRID_SLOT_M / max(length_m, 1.0)


def build_motion(timing: DriverTiming, start_progress: float, median_lap: float) -> Motion:
    """Turn lap times into progress over time.

    A pit stop's lane time is split across the timing line, because the in-lap and
    the out-lap each contain part of it, and the car waits at the line meanwhile.
    A lap far above the median has the excess treated as a stop at the end of the
    lap (red flag or long repair) instead of a very slow crawl around the circuit.
    """
    t = [0.0]
    p = [start_progress]
    pit_windows: list[tuple[float, float]] = []
    clock = 0.0
    for index, duration in enumerate(timing.durations):
        lap = index + 1
        end = clock + duration
        pit_in = min(timing.pit_laps.get(lap, 0.0), MAX_PIT_HOLD_S) / 2.0
        pit_out = min(timing.pit_laps.get(lap - 1, 0.0), MAX_PIT_HOLD_S) / 2.0
        depart = clock + pit_out
        arrive = end - pit_in
        if lap not in timing.pit_laps and median_lap > 0 and duration > STALL_RATIO * median_lap:
            arrive = min(arrive, depart + median_lap * SLOW_LAP_RATIO)
        arrive = max(arrive, depart + 0.2)
        if depart > t[-1] + 1e-6:
            t.append(depart)
            p.append(p[-1])
        t.append(arrive)
        p.append(float(lap))
        if end > arrive + 1e-6:
            t.append(end)
            p.append(float(lap))
        if pit_in > 0:
            pit_windows.append((arrive, end + pit_in))
        clock = end
    return Motion(np.array(t), np.array(p), pit_windows)


def progress_at(motion: Motion, times: np.ndarray) -> np.ndarray:
    return np.interp(times, motion.knot_t, motion.knot_p)


def median_green_lap(timings: list[DriverTiming]) -> float:
    laps = [d for tm in timings for d in tm.durations[1:]]
    return float(np.median(laps)) if laps else 0.0


def slow_windows(timings: list[DriverTiming], median_lap: float) -> list[tuple[float, float]]:
    """Time windows where most of the field was slow.

    A hint of a safety car or red flag, not a record of one: Jolpica has no
    race-control data, so the track status is labelled "inferred" on screen.
    """
    longest = max((len(tm.durations) for tm in timings), default=0)
    windows = []
    for lap in range(2, longest + 1):
        running = [tm for tm in timings if len(tm.durations) >= lap]
        if len(running) < 3:
            continue
        slow = sum(tm.durations[lap - 1] > SLOW_LAP_RATIO * median_lap for tm in running)
        if slow / len(running) >= SLOW_FIELD_SHARE:
            starts = [float(tm.lap_ends[lap - 2]) for tm in running]
            ends = [float(tm.lap_ends[lap - 1]) for tm in running]
            windows.append((min(starts), min(ends)))
    return windows


def fill_missing_laps(recorded: dict[int, float], expected: int,
                      field_median: dict[int, float], overall: float) -> tuple[list[float], int]:
    """Return lap durations 1..expected, plus how many had to be filled in."""
    durations, filled = [], 0
    for lap in range(1, expected + 1):
        if lap in recorded:
            durations.append(recorded[lap])
        else:
            durations.append(field_median.get(lap, overall))
            filled += 1
    return durations, filled


def compute_order(progress: np.ndarray, finish_time: np.ndarray, out: np.ndarray) -> np.ndarray:
    """Rank one frame: furthest round first, cars that are out last."""
    rank_keys = (finish_time, -progress, out)
    order = np.lexsort(rank_keys)
    positions = np.empty(len(order), dtype=np.uint8)
    positions[order] = np.arange(1, len(order) + 1)
    return positions


def hex_rgb(text: str | None) -> tuple[int, int, int]:
    text = (text or DEFAULT_COLOUR).strip().lstrip("#")
    if len(text) != 6:
        text = DEFAULT_COLOUR
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def did_not_start(result: dict) -> bool:
    status = result.get("status", "")
    return status in ("Did not start", "Did not qualify", "Did not prequalify", "Withdrew") or (
        result.get("positionText") in ("W", "F", "E"))


def is_classified(result: dict) -> bool:
    status = result.get("status", "")
    return status.startswith(FINISHED_STATUS) or status.startswith("+")


def _driver_code(driver: dict) -> str:
    code = driver.get("code")
    if code:
        return code
    family = rf.ascii_text(driver.get("familyName", "???")).replace(" ", "")
    return family[:3].upper()


def _car_number(result: dict) -> int:
    try:
        return int(result.get("number") or 0)
    except ValueError:
        return 0


def build(src: Sources, race: RaceInfo, outline: Outline, log=print,
          extra_notes: list[str] | None = None) -> rf.RacePackage:
    results = src.results(race)
    if not results:
        raise DataUnavailable(f"Jolpica has no results for {race.season} round {race.round}.")
    lap_data = src.lap_times(race)
    if not lap_data:
        raise DataUnavailable(
            f"Jolpica has no lap times for {race.season} round {race.round}, so no replay can be "
            "built. Nothing was estimated.")
    pit_data = src.pit_stops(race)
    log(f"  Jolpica: {len(results)} entries, {sum(map(len, lap_data.values()))} lap times, "
        f"{sum(map(len, pit_data.values()))} pit stops")

    notes = [
        "ESTIMATED positions: dots are placed from real lap times, not GPS or location data.",
        "No sector times exist for this race, so movement within each lap is modelled.",
        "Exact retirement points are unknown: a retired car leaves at its last recorded lap.",
    ]
    notes += extra_notes or []
    flags = 0
    if outline.schematic:
        flags |= rf.FLAG_SCHEMATIC
        notes.append("No real outline for this circuit: the track shape is a schematic oval.")
    else:
        notes.append(f"Track outline: {outline.source}. The layout in {race.season} may differ.")
    warning = layout_warning(race.circuit_id, race.season, race.name)
    if warning and not outline.schematic:
        flags |= rf.FLAG_LAYOUT_DIFFERS
        notes.append(warning)
    if race.season < FIRST_PIT_STOP_SEASON:
        notes.append("No pit stop data before 2011: stops show only as slower laps.")
    elif not pit_data:
        notes.append("No pit stop data for this race: stops show only as slower laps.")

    per_lap: dict[int, list[float]] = {}
    for laps in lap_data.values():
        for lap, (seconds, _) in laps.items():
            per_lap.setdefault(lap, []).append(seconds)
    field_median = {lap: float(np.median(vals)) for lap, vals in per_lap.items()}
    overall = float(np.median(list(field_median.values())))

    drivers: list[rf.Driver] = []
    timings: list[DriverTiming] = []
    team_seen: set[str] = set()
    filled_total = expected_total = 0
    no_start: list[str] = []
    for result in sorted(results, key=lambda r: int(r.get("position") or 99)):
        info = result["Driver"]
        team_id = result["Constructor"]["constructorId"]
        recorded = {lap: v[0] for lap, v in lap_data.get(info["driverId"], {}).items()}
        expected = max(int(result.get("laps") or 0), max(recorded, default=0))
        name = f"{info.get('givenName', '')} {info.get('familyName', '')}".strip()
        if did_not_start(result) and not recorded:
            no_start.append(name)
            continue
        durations, filled = fill_missing_laps(recorded, expected, field_median, overall)
        filled_total += filled
        expected_total += expected
        classified = is_classified(result)
        grid = int(result.get("grid") or 0)
        drivers.append(rf.Driver(
            _car_number(result), _driver_code(info), name, result["Constructor"]["name"],
            hex_rgb(TEAM_COLOURS.get(team_id)), grid,
            int(result["position"]) if classified else 0, team_id in team_seen))
        team_seen.add(team_id)
        pits = {lap: (sec if 0 < sec <= MAX_PIT_HOLD_S else DEFAULT_PIT_HOLD_S)
                for lap, sec in pit_data.get(info["driverId"], {}).items()}
        timings.append(DriverTiming(durations, pits, grid, classified))

    if no_start:
        notes.append("Did not start: " + ", ".join(no_start) + ".")
    if expected_total and filled_total:
        share = filled_total / expected_total
        notes.append(f"{filled_total} of {expected_total} lap times were missing from the source "
                     "and were filled with that lap's field median.")
        if share > FILL_WARNING_SHARE:
            flags |= rf.FLAG_INCOMPLETE
    winner = next((r for r in results if r.get("position") == "1"), None)
    if winner and winner.get("Time", {}).get("millis") and timings:
        recorded_total = sum(timings[0].durations)
        official = int(winner["Time"]["millis"]) / 1000.0
        if abs(recorded_total - official) > 2.0:
            notes.append(f"Winner's lap times sum to {recorded_total:.1f}s but the official time "
                         f"is {official:.1f}s; the source lap data may be incomplete.")

    median_lap = median_green_lap(timings)
    n = len(drivers)
    total_laps = max(len(tm.durations) for tm in timings)
    race_end = max(float(tm.lap_ends[-1]) for tm in timings if tm.durations)
    n_frames = int(math.ceil((PRE_ROLL_S + race_end + POST_ROLL_S) / FRAME_DT_S)) + 1
    times = np.arange(n_frames) * FRAME_DT_S - PRE_ROLL_S

    progress = np.zeros((n_frames, n))
    state = np.zeros((n_frames, n), np.uint8)
    finish_times = np.full(n, np.inf)
    laps_table: list[rf.LapRecord] = []
    for i, timing in enumerate(timings):
        start = -grid_offset(timing.grid, n, outline.length_m)
        motion = build_motion(timing, start, median_lap)
        progress[:, i] = progress_at(motion, times)
        for begin, end in motion.pit_windows:
            state[(times >= begin) & (times < end), i] = rf.STATE_PIT
        ends = timing.lap_ends
        last = float(ends[-1]) if len(ends) else 0.0
        if timing.classified:
            state[times >= last, i] = rf.STATE_FINISHED
            finish_times[i] = last
        else:
            state[times >= last, i] = rf.STATE_OUT
        for lap, (duration, end) in enumerate(zip(timing.durations, ends), start=1):
            flag = rf.LAP_PIT_IN if lap in timing.pit_laps else 0
            flag |= rf.LAP_PIT_OUT if lap - 1 in timing.pit_laps else 0
            laps_table.append(rf.LapRecord(i, lap, flag, int(end * 1000), int(duration * 1000)))

    xy = outline.fraction_to_xy(progress.ravel())
    transform = fit_transform(outline.points)
    mapped = transform.apply(xy)
    x = mapped[:, 0].reshape(n_frames, n)
    y = mapped[:, 1].reshape(n_frames, n)

    position = np.zeros((n_frames, n), np.uint8)
    out_mask = state == rf.STATE_OUT
    finish_rank = np.where(np.isinf(finish_times), 0.0, finish_times)
    for frame in range(n_frames):
        position[frame] = compute_order(progress[frame], finish_rank, out_mask[frame])
    official = [d.final_position for d in drivers]
    if all(official[i] > 0 or not timings[i].classified for i in range(n)):
        final = position[-1].copy()
        classified_rows = sorted((i for i in range(n) if official[i]), key=lambda i: official[i])
        for rank, i in enumerate(classified_rows, start=1):
            final[i] = rank
        others = sorted((i for i in range(n) if not official[i]), key=lambda i: position[-1][i])
        for rank, i in enumerate(others, start=len(classified_rows) + 1):
            final[i] = rank
        position[-1] = final
        notes.append("The last frame shows the official classification, including any penalties.")

    completed = np.floor(np.maximum(progress, 0)).astype(np.int32)
    lap = np.where(times[:, None] < 0, 0, np.minimum(completed + 1, total_laps))
    for i, timing in enumerate(timings):
        lap[:, i] = np.minimum(lap[:, i], max(len(timing.durations), 1))
    gap = _gaps(timings, completed, n_frames, n)
    leader_lap = lap.max(axis=1).astype(np.uint8)

    track_status = np.zeros(n_frames, np.uint8)
    windows = slow_windows(timings, median_lap)
    for begin, end in windows:
        track_status[(times >= begin) & (times < end)] = rf.TRACK_SLOW
    if windows:
        notes.append("SLOW* marks laps where most cars slowed: inferred from timing, it may be a "
                     "safety car, red flag or weather. No race-control data exists for this race.")

    return rf.RacePackage(
        season=race.season, round=race.round, fidelity=rf.FIDELITY_ESTIMATED,
        total_laps=total_laps, race_name=race.name, circuit=race.circuit_name, date=race.date,
        sources="Jolpica F1 (results, lap times, pit stops)", outline_source=outline.source,
        start_epoch_ms=int((race.start_utc - PRE_ROLL_S) * 1000),
        lights_out_ms=int(PRE_ROLL_S * 1000), map_w=transform.map_w, map_h=transform.map_h,
        outline=transform.apply(outline.points), drivers=drivers, laps=laps_table, x=x, y=y,
        state=state, no_data=np.zeros((n_frames, n), bool),
        estimated=np.ones((n_frames, n), bool), position=position, lap=lap, gap=gap,
        track_status=track_status, leader_lap=leader_lap, flags=flags, notes=notes)


def _gaps(timings: list[DriverTiming], completed: np.ndarray, n_frames: int, n: int) -> np.ndarray:
    """Time behind the leader at each car's most recent timing-line crossing (real values)."""
    longest = max(len(tm.durations) for tm in timings)
    best = np.full(longest + 1, np.inf)
    ends = np.full((n, longest + 1), np.nan)
    for i, timing in enumerate(timings):
        count = len(timing.durations)
        ends[i, 1:count + 1] = timing.lap_ends
        best[1:count + 1] = np.minimum(best[1:count + 1], timing.lap_ends)
    gap = np.full((n_frames, n), rf.GAP_UNKNOWN, np.uint16)
    for i in range(n):
        lap_index = np.minimum(completed[:, i], longest)
        behind = ends[i, lap_index] - best[lap_index]
        known = (lap_index > 0) & np.isfinite(behind)
        gap[known, i] = np.minimum(behind[known] * 10, rf.GAP_LAPS_BASE - 1).astype(np.uint16)
    return gap
