# Race package format

Version 1 of the files the data tool writes and the firmware reads. All
integers are little-endian. Text is plain ASCII (accents are removed for the
device font; full UTF-8 names stay in the `reports/*.json` sidecar).

If you change a layout, raise the version in `data_tool/f1data/race_format.py`
and `firmware/F1ReplayCYD/race_format.h`. The firmware refuses versions it does
not know and tells you to update.

## Folder layout on the microSD card

```
/f1replay/manifest.txt         catalog of races
/f1replay/races/2024_01.f1r    one race package per race
/f1replay/reports/2024_01.json human-readable provenance (not read by the firmware)
/f1replay/wifi.txt             optional, only for Wi-Fi sync
```

## manifest.txt

Tab-separated text, one line per race, sorted by season and round. The first
line is `F1RM<TAB>1<TAB><generated UTC time>`. A line is at most 255 bytes.

| # | Field | Meaning |
|---|-------|---------|
| 1 | season | e.g. `2024` |
| 2 | round | e.g. `1` |
| 3 | status | `R` ready, `N` can be built but has not been, `U` unavailable, `F` future or unfinished |
| 4 | fidelity | `T` telemetry, `E` estimated, `-` none |
| 5 | file | e.g. `2024_01.f1r`, or `-` |
| 6 | size | bytes, `0` if no file |
| 7 | crc32 | whole-file CRC-32 in hex, `0` if no file |
| 8 | name | race name |
| 9 | circuit | circuit name |
| 10 | date | `YYYY-MM-DD` |
| 11 | note | explanation of missing or limited data; may be empty |

## .f1r race package

```
header (128 bytes) | strings | drivers | outline | lap records | frames
```

### Header

| Offset | Type | Field |
|-------:|------|-------|
| 0 | char[4] | magic `F1RP` |
| 4 | u16 | format version (1) |
| 6 | u16 | header size (128) |
| 8 | u8 | fidelity: 1 telemetry, 2 estimated |
| 9 | u8 | number of drivers |
| 10 | u16 | flags, see below |
| 12 | u16 | season |
| 14 | u8 | round |
| 15 | u8 | reserved |
| 16 | u16 | total race laps |
| 18 | u16 | frame interval in ms (500) |
| 20 | u32 | number of frames |
| 24 | u32 | lights-out time, ms after the first frame |
| 28 | u64 | UTC time of the first frame, ms since the Unix epoch |
| 36 | u16 | map width in map units |
| 38 | u16 | map height in map units |
| 40 | u16 | outline point count |
| 42 | u16 | frame stride in bytes (`4 + 8 * drivers`) |
| 44 | u32 | lap record count |
| 48 | u32 | strings offset |
| 52 | u32 | strings length |
| 56 | u32 | drivers offset |
| 60 | u32 | outline offset |
| 64 | u32 | lap records offset |
| 68 | u32 | frames offset |
| 72 | u32 | total file size |
| 76 | u8[48] | reserved, zero |
| 124 | u32 | CRC-32 of bytes 0-123 |

Flags: `1` schematic outline (no real shape available), `2` the race used a
different layout from the outline, `4` some cars in a telemetry race are
estimated, `8` source data is incomplete (see the notes).

### Strings

Repeated `u8 id, u16 length, bytes`. Ids: 1 race name, 2 circuit, 3 date,
4 data sources, 5 outline source, 10 note (may repeat).

### Drivers (64 bytes each)

| Offset | Type | Field |
|-------:|------|-------|
| 0 | u16 | car number |
| 2 | char[4] | three-letter code, NUL padded |
| 6 | char[24] | full name |
| 30 | char[24] | team |
| 54 | u16 | display colour, RGB565, lightened for a dark screen |
| 56 | u8[3] | source colour, RGB |
| 59 | u8 | grid position, 0 unknown or pit lane |
| 60 | u8 | official finishing position, 0 not classified |
| 61 | u8 | bit 0: second car of the team (drawn with a ring) |
| 62 | u16 | reserved |

### Outline

`u16 x, u16 y` per point, in map units. Y increases downward (screen style).
The points form a closed loop in racing direction, starting at the start line.

### Lap records (12 bytes each)

The real lap timing, sorted by driver and lap.

| Offset | Type | Field |
|-------:|------|-------|
| 0 | u8 | driver index |
| 1 | u8 | lap number |
| 2 | u8 | bit 0 pit in, bit 1 pit out, bit 2 filled in because the source had no value |
| 3 | u8 | reserved |
| 4 | u32 | lap end, ms after lights-out |
| 8 | u32 | lap duration, ms |

### Frames

Fixed stride, so frame `i` is at `frames offset + i * stride`. The firmware
reads the two frames around the playback time and interpolates, which makes
seeking instant and keeps the race out of RAM.

Frame header (4 bytes):

| Offset | Type | Field |
|-------:|------|-------|
| 0 | u8 | track status: 0 green, 1 yellow, 2 safety car, 3 virtual safety car, 4 red, 5 slow laps inferred from timing |
| 1 | u8 | leader's lap |
| 2 | u16 | reserved |

Then one 8-byte record per driver, in driver-table order:

| Offset | Type | Field |
|-------:|------|-------|
| 0 | u16 | bits 0-11 x; bits 12-13 state; bit 14 no data; bit 15 estimated |
| 2 | u16 | bits 0-11 y |
| 4 | u8 | running position, 1 = leader |
| 5 | u8 | current lap |
| 6 | u16 | gap to leader in 0.1 s; `0xFF00 + n` = n laps down; `0xFFFF` unknown |

State: 0 running, 1 in the pit lane, 2 out of the race, 3 finished.

"No data" means the source has no location for this moment: x and y hold the
last known position and the firmware draws a hollow dot. "Estimated" means the
position came from lap timing rather than location samples.
