"""Read and write the versioned .f1r race package. See docs/FORMAT.md."""

from __future__ import annotations

import struct
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MAGIC = b"F1RP"
VERSION = 1
HEADER_SIZE = 128
DRIVER_SIZE = 64
LAP_RECORD_SIZE = 12
FRAME_HEADER_SIZE = 4
CAR_SIZE = 8
MAP_UNITS = 4095

FIDELITY_TELEMETRY = 1
FIDELITY_ESTIMATED = 2

FLAG_SCHEMATIC = 1
FLAG_LAYOUT_DIFFERS = 2
FLAG_MIXED_ESTIMATED = 4
FLAG_INCOMPLETE = 8

STATE_RUNNING, STATE_PIT, STATE_OUT, STATE_FINISHED = 0, 1, 2, 3
BIT_NO_DATA = 1 << 14
BIT_ESTIMATED = 1 << 15

TRACK_GREEN, TRACK_YELLOW, TRACK_SC, TRACK_VSC, TRACK_RED, TRACK_SLOW = range(6)

GAP_UNKNOWN = 0xFFFF
GAP_LAPS_BASE = 0xFF00

STR_RACE, STR_CIRCUIT, STR_DATE, STR_SOURCES, STR_OUTLINE, STR_NOTE = 1, 2, 3, 4, 5, 10

LAP_PIT_IN, LAP_PIT_OUT, LAP_FILLED = 1, 2, 4

_HEADER = struct.Struct("<4sHHBBHHBBHHIIQHHHHIIIIIIII48s")
_DRIVER = struct.Struct("<H4s24s24sH3sBBBH")
_LAP = struct.Struct("<BBBBII")
assert _HEADER.size == HEADER_SIZE - 4
assert _DRIVER.size == DRIVER_SIZE
assert _LAP.size == LAP_RECORD_SIZE


def ascii_text(text: str) -> str:
    """Reduce text to printable ASCII for the device font (e.g. accents are removed)."""
    for src, dst in (("ß", "ss"), ("ø", "o"), ("Ø", "O"), ("ł", "l")):
        text = text.replace(src, dst)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if 32 <= ord(c) < 127)


def rgb565(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def display_colour(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Lighten colours too dark to see against the black map."""
    r, g, b = rgb
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    if luma >= 90:
        return rgb
    mix = (90 - luma) / (255 - luma)
    return tuple(int(c + (255 - c) * mix) for c in rgb)


@dataclass
class Driver:
    number: int
    code: str
    name: str
    team: str
    rgb: tuple[int, int, int]
    grid: int = 0
    final_position: int = 0
    second_car: bool = False


@dataclass
class LapRecord:
    driver_index: int
    lap: int
    flags: int
    end_ms: int
    duration_ms: int


@dataclass
class RacePackage:
    """A complete race. Frame arrays are shaped (frames, drivers)."""

    season: int
    round: int
    fidelity: int
    total_laps: int
    race_name: str
    circuit: str
    date: str
    sources: str
    outline_source: str
    start_epoch_ms: int
    lights_out_ms: int
    map_w: int
    map_h: int
    outline: np.ndarray
    drivers: list[Driver]
    laps: list[LapRecord]
    x: np.ndarray
    y: np.ndarray
    state: np.ndarray
    no_data: np.ndarray
    estimated: np.ndarray
    position: np.ndarray
    lap: np.ndarray
    gap: np.ndarray
    track_status: np.ndarray
    leader_lap: np.ndarray
    frame_dt_ms: int = 500
    flags: int = 0
    notes: list[str] = field(default_factory=list)


def _fixed(text: str, size: int) -> bytes:
    return ascii_text(text).encode("ascii")[:size - 1].ljust(size, b"\0")


def _strings(pkg: RacePackage) -> bytes:
    items = [(STR_RACE, pkg.race_name), (STR_CIRCUIT, pkg.circuit), (STR_DATE, pkg.date),
             (STR_SOURCES, pkg.sources), (STR_OUTLINE, pkg.outline_source)]
    items += [(STR_NOTE, note) for note in pkg.notes]
    out = bytearray()
    for sid, text in items:
        raw = ascii_text(text).encode("ascii")[:400]
        out += struct.pack("<BH", sid, len(raw)) + raw
    return bytes(out)


def _drivers(pkg: RacePackage) -> bytes:
    out = bytearray()
    for d in pkg.drivers:
        out += _DRIVER.pack(d.number, _fixed(d.code, 4), _fixed(d.name, 24), _fixed(d.team, 24),
                            rgb565(display_colour(d.rgb)), bytes(d.rgb), d.grid,
                            d.final_position, 1 if d.second_car else 0, 0)
    return bytes(out)


def _frame_dtype(n_drivers: int) -> np.dtype:
    return np.dtype([("status", "u1"), ("leader", "u1"), ("reserved", "<u2"),
                     ("cars", [("xs", "<u2"), ("y", "<u2"), ("pos", "u1"), ("lap", "u1"),
                               ("gap", "<u2")], (n_drivers,))])


def _frames(pkg: RacePackage) -> bytes:
    n_frames, n_drivers = pkg.x.shape
    frames = np.zeros(n_frames, dtype=_frame_dtype(n_drivers))
    frames["status"] = pkg.track_status
    frames["leader"] = pkg.leader_lap
    xs = np.clip(pkg.x, 0, MAP_UNITS).astype(np.uint16) | (pkg.state.astype(np.uint16) << 12)
    xs |= np.where(pkg.no_data, BIT_NO_DATA, 0).astype(np.uint16)
    xs |= np.where(pkg.estimated, BIT_ESTIMATED, 0).astype(np.uint16)
    frames["cars"]["xs"] = xs
    frames["cars"]["y"] = np.clip(pkg.y, 0, MAP_UNITS).astype(np.uint16)
    frames["cars"]["pos"] = pkg.position
    frames["cars"]["lap"] = np.clip(pkg.lap, 0, 255)
    frames["cars"]["gap"] = pkg.gap
    return frames.tobytes()


def serialize(pkg: RacePackage) -> bytes:
    strings = _strings(pkg)
    drivers = _drivers(pkg)
    outline = np.clip(pkg.outline, 0, MAP_UNITS).astype("<u2").tobytes()
    laps = b"".join(_LAP.pack(r.driver_index, min(r.lap, 255), r.flags, 0, r.end_ms,
                              r.duration_ms) for r in pkg.laps)
    frames = _frames(pkg)
    n_frames, n_drivers = pkg.x.shape

    strings_off = HEADER_SIZE
    drivers_off = strings_off + len(strings)
    outline_off = drivers_off + len(drivers)
    laps_off = outline_off + len(outline)
    frames_off = laps_off + len(laps)
    file_size = frames_off + len(frames)

    head = _HEADER.pack(
        MAGIC, VERSION, HEADER_SIZE, pkg.fidelity, n_drivers, pkg.flags, pkg.season, pkg.round, 0,
        pkg.total_laps, pkg.frame_dt_ms, n_frames, pkg.lights_out_ms, pkg.start_epoch_ms,
        pkg.map_w, pkg.map_h, len(pkg.outline), FRAME_HEADER_SIZE + CAR_SIZE * n_drivers,
        len(pkg.laps), strings_off, len(strings), drivers_off, outline_off, laps_off, frames_off,
        file_size, bytes(48))
    head += struct.pack("<I", zlib.crc32(head))
    return head + strings + drivers + outline + laps + frames


def write_package(pkg: RacePackage, path: Path) -> tuple[int, int]:
    """Write the package atomically. Returns (size, crc32)."""
    data = serialize(pkg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return len(data), zlib.crc32(data)


class BadRaceFile(Exception):
    pass


def read_package(path: Path) -> dict:
    """Parse and validate a package (used by `verify`, `preview` and the tests)."""
    data = Path(path).read_bytes()
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise BadRaceFile("not a race package")
    (_, version, _, fidelity, n_drivers, flags, season, rnd, _, total_laps, dt, n_frames,
     lights_out, start_epoch, map_w, map_h, n_outline, stride, n_laps, strings_off, strings_len,
     drivers_off, outline_off, laps_off, frames_off, file_size, _) = _HEADER.unpack(
        data[:HEADER_SIZE - 4])
    if version != VERSION:
        raise BadRaceFile(f"format version {version}, expected {VERSION}")
    stored_crc = struct.unpack("<I", data[HEADER_SIZE - 4:HEADER_SIZE])[0]
    if zlib.crc32(data[:HEADER_SIZE - 4]) != stored_crc:
        raise BadRaceFile("header checksum mismatch")
    if file_size != len(data) or frames_off + n_frames * stride != len(data):
        raise BadRaceFile("file is truncated or the wrong size")

    strings: dict[int, list[str]] = {}
    pos = strings_off
    while pos < strings_off + strings_len:
        sid, length = struct.unpack_from("<BH", data, pos)
        pos += 3
        strings.setdefault(sid, []).append(data[pos:pos + length].decode("ascii"))
        pos += length

    drivers = []
    for i in range(n_drivers):
        number, code, name, team, colour, rgb, grid, final, flag, _ = _DRIVER.unpack_from(
            data, drivers_off + i * DRIVER_SIZE)
        drivers.append({"number": number, "code": code.rstrip(b"\0").decode(),
                        "name": name.rstrip(b"\0").decode(), "team": team.rstrip(b"\0").decode(),
                        "rgb565": colour, "rgb": tuple(rgb), "grid": grid, "final": final,
                        "second_car": bool(flag & 1)})

    outline = np.frombuffer(data, "<u2", n_outline * 2, outline_off).reshape(-1, 2)
    laps = [_LAP.unpack_from(data, laps_off + i * LAP_RECORD_SIZE) for i in range(n_laps)]
    frames = np.frombuffer(data, _frame_dtype(n_drivers), n_frames, frames_off)
    return {"version": version, "fidelity": fidelity, "flags": flags, "season": season,
            "round": rnd, "total_laps": total_laps, "frame_dt_ms": dt, "n_frames": n_frames,
            "lights_out_ms": lights_out, "start_epoch_ms": start_epoch, "map_w": map_w,
            "map_h": map_h, "strings": strings, "drivers": drivers, "outline": outline,
            "laps": laps, "frames": frames, "crc32": zlib.crc32(data), "size": len(data)}
