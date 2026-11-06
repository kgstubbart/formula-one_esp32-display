// Reads manifest.txt line by line, so catalog size does not affect RAM use.
#pragma once

#include <stdint.h>

#include "byte_source.h"

constexpr int MAX_SEASONS = 80;
constexpr int MAX_RACES_PER_SEASON = 32;
constexpr int MANIFEST_MAX_LINE = 256;

enum class ManifestError : uint8_t { None, Empty, NotAManifest, WrongVersion };

// One manifest row, with its text fields pointing into `line`.
struct ManifestRow {
  uint16_t season;
  uint8_t round;
  char status;    // R ready, N not built, U unavailable, F future.
  char fidelity;  // T telemetry, E estimated, - none.
  uint32_t size;
  uint32_t crc32;
  const char* file;
  const char* name;
  const char* circuit;
  const char* date;
  const char* note;
  char line[MANIFEST_MAX_LINE];
};

// The short form kept in RAM for the race list.
struct RaceListItem {
  uint8_t round;
  char status;
  char fidelity;
  char name[33];
};

class Manifest {
 public:
  ManifestError open(ByteSource* source);

  // Rewinds to the first race. Call nextRow() until it returns false.
  void rewind();
  bool nextRow(ManifestRow* row);

  // Returns how many were found, sorted newest first.
  int collectSeasons(uint16_t* out, int maxSeasons);
  int collectRaces(uint16_t season, RaceListItem* out, int maxRaces);
  bool findRace(uint16_t season, uint8_t round, ManifestRow* row);

 private:
  bool readLine(char* dst, int dstSize);
  int nextByte();

  ByteSource* source_ = nullptr;
  uint32_t firstRowOffset_ = 0;
  uint32_t bufferOffset_ = 0;
  uint8_t buffer_[256];
  int bufferLength_ = 0;
  int bufferIndex_ = 0;
};

// Parses a line in place. Returns false if the line is malformed.
bool parseManifestLine(ManifestRow* row);
