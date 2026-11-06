// Constants for the race package format. The full description is docs/FORMAT.md.
// This must stay in step with data_tool/f1data/race_format.py.
#pragma once

#include <stddef.h>
#include <stdint.h>

constexpr uint16_t RACE_FORMAT_VERSION = 1;
constexpr uint16_t MANIFEST_FORMAT_VERSION = 1;
constexpr uint32_t RACE_HEADER_SIZE = 128;
constexpr uint32_t RACE_DRIVER_SIZE = 64;
constexpr uint32_t RACE_LAP_RECORD_SIZE = 12;
constexpr uint32_t RACE_FRAME_HEADER_SIZE = 4;
constexpr uint32_t RACE_CAR_SIZE = 8;

constexpr uint8_t FIDELITY_TELEMETRY = 1;
constexpr uint8_t FIDELITY_ESTIMATED = 2;

constexpr uint16_t RACE_FLAG_SCHEMATIC = 1;
constexpr uint16_t RACE_FLAG_LAYOUT_DIFFERS = 2;
constexpr uint16_t RACE_FLAG_MIXED_ESTIMATED = 4;
constexpr uint16_t RACE_FLAG_INCOMPLETE = 8;

enum CarState : uint8_t { CAR_RUNNING = 0, CAR_PIT = 1, CAR_OUT = 2, CAR_FINISHED = 3 };

enum TrackStatus : uint8_t {
  TRACK_GREEN = 0,
  TRACK_YELLOW = 1,
  TRACK_SC = 2,
  TRACK_VSC = 3,
  TRACK_RED = 4,
  TRACK_SLOW_INFERRED = 5
};

constexpr uint16_t GAP_UNKNOWN = 0xFFFF;
constexpr uint16_t GAP_LAPS_BASE = 0xFF00;

constexpr uint8_t STR_RACE = 1;
constexpr uint8_t STR_CIRCUIT = 2;
constexpr uint8_t STR_DATE = 3;
constexpr uint8_t STR_SOURCES = 4;
constexpr uint8_t STR_OUTLINE = 5;
constexpr uint8_t STR_NOTE = 10;

constexpr uint8_t LAP_FLAG_PIT_IN = 1;
constexpr uint8_t LAP_FLAG_PIT_OUT = 2;
constexpr uint8_t LAP_FLAG_FILLED = 4;

// CRC-32 (the same polynomial as Python's zlib.crc32). Pass the previous result to continue.
uint32_t crc32Update(uint32_t crc, const void* data, size_t length);
