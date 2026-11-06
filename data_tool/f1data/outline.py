"""Track outlines: where they come from, and how they map onto the small screen."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .circuits import CIRCUITS, OUTLINE_GEOJSON_URL
from .race_format import MAP_UNITS
from .sources import Sources, iso, parse_time

OUTLINE_POINTS = 256
MAP_ASPECT = 224 / 188      # Width / height of the firmware's map area.
MAP_MARGIN = 0.02
LOCATION_UNITS_PER_M = 10.0  # OpenF1 x/y are in decimetres.
MAX_LOOP_GAP_M = 150.0
MAX_SAMPLE_STEP_M = 100.0    # The feed sometimes skips part of a lap in one jump.
MIN_LAP_COVERAGE = 0.95
MAX_REFERENCE_LAPS = 4


@dataclass
class Outline:
    """A closed loop in metres, racing direction, starting at the start line."""

    points: np.ndarray
    source: str
    length_m: float
    profile_t: np.ndarray | None = None  # Fraction of lap time ...
    profile_d: np.ndarray | None = None  # ... to fraction of lap distance.
    schematic: bool = False
    telemetry_based: bool = False

    def fraction_to_xy(self, fraction: np.ndarray) -> np.ndarray:
        """Map lap *time* fraction(s) to (x, y), using the speed profile when there is one."""
        fraction = np.mod(fraction, 1.0)
        distance = fraction
        if self.profile_t is not None:
            distance = np.interp(fraction, self.profile_t, self.profile_d)
        loop = np.vstack([self.points, self.points[:1]])
        arc = _arc_length(loop)
        target = distance * arc[-1]
        return np.column_stack([np.interp(target, arc, loop[:, 0]),
                                np.interp(target, arc, loop[:, 1])])


def _arc_length(points: np.ndarray) -> np.ndarray:
    steps = np.hypot(*np.diff(points, axis=0).T)
    return np.concatenate([[0.0], np.cumsum(steps)])


def resample_closed(points: np.ndarray, count: int = OUTLINE_POINTS) -> np.ndarray:
    """Resample an open-ended loop to evenly spaced points."""
    loop = np.vstack([points, points[:1]])
    arc = _arc_length(loop)
    keep = np.concatenate([[True], np.diff(arc) > 1e-9])
    loop, arc = loop[keep], arc[keep]
    target = np.linspace(0.0, arc[-1], count, endpoint=False)
    return np.column_stack([np.interp(target, arc, loop[:, 0]), np.interp(target, arc, loop[:, 1])])


def smooth_closed(points: np.ndarray, window: int = 3) -> np.ndarray:
    kernel = np.ones(window) / window
    pad = window
    wrapped = np.vstack([points[-pad:], points, points[:pad]])
    return np.column_stack([np.convolve(wrapped[:, i], kernel, "same")[pad:-pad] for i in (0, 1)])


def outline_from_samples(t: np.ndarray, x: np.ndarray, y: np.ndarray, start: float,
                         duration: float, source: str) -> Outline | None:
    """Build an outline from one lap of location samples, or None if the lap is unusable."""
    inside = (t >= start) & (t <= start + duration)
    if inside.sum() < duration * 2.0:
        return None
    lap_t = t[inside]
    if np.max(np.diff(lap_t)) > 2.0 or lap_t[-1] - lap_t[0] < MIN_LAP_COVERAGE * duration:
        return None
    pts = np.column_stack([x[inside], y[inside]]) / LOCATION_UNITS_PER_M
    if np.hypot(*(pts[0] - pts[-1])) > MAX_LOOP_GAP_M:
        return None
    if np.max(np.hypot(*np.diff(pts, axis=0).T)) > MAX_SAMPLE_STEP_M:
        return None
    open_arc = _arc_length(pts)
    profile_t = (lap_t - lap_t[0]) / (lap_t[-1] - lap_t[0])
    profile_d = open_arc / open_arc[-1]
    points = smooth_closed(resample_closed(pts))
    length = float(_arc_length(np.vstack([points, points[:1]]))[-1])
    return Outline(points, source, length, profile_t, profile_d, telemetry_based=True)


def clean_lap_candidates(laps: list[dict]) -> list[dict]:
    """Quickest laps first, skipping out-laps and laps with a missing time."""
    usable = [lap for lap in laps if lap.get("lap_duration") and lap.get("date_start")
              and not lap.get("is_pit_out_lap") and lap.get("lap_number", 0) > 1]
    return sorted(usable, key=lambda lap: lap["lap_duration"])[:8]


def telemetry_outline(src: Sources, circuit_id: str) -> Outline | None:
    """Reference outline for this circuit from the newest OpenF1 race held there."""
    short_name = CIRCUITS.get(circuit_id, (None, None))[0]
    if short_name is None:
        return None
    for session in src.circuit_sessions(short_name)[:3]:
        key = session["session_key"]
        for lap in clean_lap_candidates(src.session_data("laps", key))[:MAX_REFERENCE_LAPS]:
            start = parse_time(lap["date_start"])
            rows = src.session_data("location", key, [
                ("driver_number", "=", lap["driver_number"]),
                ("date", ">=", iso(start - 1)),
                ("date", "<=", iso(start + lap["lap_duration"] + 1))])
            if not rows:
                continue
            t = np.array([parse_time(r["date"]) for r in rows])
            x = np.array([r["x"] for r in rows], float)
            y = np.array([r["y"] for r in rows], float)
            source = f"OpenF1 telemetry, {session['year']} race lap"
            outline = outline_from_samples(t, x, y, start, lap["lap_duration"], source)
            if outline is not None:
                return outline
    return None


def geojson_outline(src: Sources, circuit_id: str) -> Outline | None:
    """Geographic outline from the bacinger/f1-circuits data set (MIT licence)."""
    feature_id = CIRCUITS.get(circuit_id, (None, None))[1]
    if feature_id is None:
        return None
    path = OUTLINE_GEOJSON_URL.split("githubusercontent.com/", 1)[1]
    data = src.raw.get(path, ttl_s=30 * 24 * 3600)
    for feature in data.get("features", []):
        if feature["properties"].get("id") == feature_id:
            lonlat = np.array(feature["geometry"]["coordinates"], float)[:, :2]
            lat0 = math.radians(float(lonlat[:, 1].mean()))
            pts = np.column_stack([(lonlat[:, 0] - lonlat[:, 0].mean()) * 111320 * math.cos(lat0),
                                   (lonlat[:, 1] - lonlat[:, 1].mean()) * 110540])
            if np.hypot(*(pts[0] - pts[-1])) < 1.0:
                pts = pts[:-1]
            points = resample_closed(pts)
            length = float(_arc_length(np.vstack([points, points[:1]]))[-1])
            return Outline(points, "bacinger/f1-circuits GeoJSON (start line approximate)", length)
    return None


def schematic_outline() -> Outline:
    """Plain stadium shape for circuits with no outline source. Always labelled as such."""
    angles = np.linspace(0, 2 * np.pi, OUTLINE_POINTS, endpoint=False)
    power = 0.5
    x = np.sign(np.cos(angles)) * np.abs(np.cos(angles)) ** power * 1600
    y = np.sign(np.sin(angles)) * np.abs(np.sin(angles)) ** power * 800
    points = resample_closed(np.column_stack([x, y]))
    length = float(_arc_length(np.vstack([points, points[:1]]))[-1])
    return Outline(points, "Schematic oval: no real outline available", length, schematic=True)


def best_outline(src: Sources, circuit_id: str) -> Outline:
    return (telemetry_outline(src, circuit_id) or geojson_outline(src, circuit_id)
            or schematic_outline())


@dataclass
class MapTransform:
    """Rotates and scales metres into map units, with y pointing down the screen."""

    angle: float
    scale: float
    min_x: float
    max_y: float
    map_w: int
    map_h: int

    def apply(self, points: np.ndarray) -> np.ndarray:
        rotated = _rotate(points, self.angle)
        margin = MAP_MARGIN * MAP_UNITS
        return np.column_stack([(rotated[:, 0] - self.min_x) * self.scale + margin,
                                (self.max_y - rotated[:, 1]) * self.scale + margin])


def _rotate(points: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return points @ np.array([[c, s], [-s, c]])


def fit_transform(points: np.ndarray, aspect: float = MAP_ASPECT) -> MapTransform:
    """Pick the rotation that lets the circuit fill the map area best.

    Rotation only, never a mirror image, so corners keep their real handedness.
    """
    best = None
    for degrees in range(0, 180, 3):
        angle = math.radians(degrees)
        rotated = _rotate(points, angle)
        width, height = np.ptp(rotated, axis=0)
        fill = min(aspect / width, 1.0 / height)
        if best is None or fill > best[0]:
            best = (fill, angle, rotated, width, height)
    _, angle, rotated, width, height = best
    usable = MAP_UNITS * (1 - 2 * MAP_MARGIN)
    scale = usable / max(width, height * aspect)
    margin = 2 * MAP_MARGIN * MAP_UNITS
    return MapTransform(angle, scale, float(rotated[:, 0].min()), float(rotated[:, 1].max()),
                        int(width * scale + margin), int(height * scale + margin))
