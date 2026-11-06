// Reads a .f1r race package. Only the small tables stay in RAM. Frames are read
// from the card on demand, two at a time.
#pragma once

#include <stdint.h>

#include "byte_source.h"
#include "race_format.h"

constexpr int MAX_DRIVERS = 32;
constexpr int MAX_OUTLINE_POINTS = 512;
constexpr int MAX_LAPS_PER_DRIVER = 128;
constexpr uint32_t MAX_FRAME_SIZE = RACE_FRAME_HEADER_SIZE + RACE_CAR_SIZE * MAX_DRIVERS;

enum class RaceError : uint8_t {
  None,
  NotARaceFile,
  WrongVersion,
  BadChecksum,
  WrongSize,
  BadLayout,
  ReadFailed
};

const char* raceErrorText(RaceError error);

struct RaceHeader {
  uint16_t version;
  uint8_t fidelity;
  uint8_t driverCount;
  uint16_t flags;
  uint16_t season;
  uint8_t round;
  uint16_t totalLaps;
  uint16_t frameDtMs;
  uint32_t frameCount;
  uint32_t lightsOutMs;
  uint64_t startEpochMs;
  uint16_t mapW;
  uint16_t mapH;
  uint16_t outlinePoints;
  uint16_t frameStride;
  uint32_t lapRecords;
  uint32_t stringsOffset;
  uint32_t stringsLength;
  uint32_t driversOffset;
  uint32_t outlineOffset;
  uint32_t lapsOffset;
  uint32_t framesOffset;
  uint32_t fileSize;
};

struct DriverInfo {
  uint16_t number;
  char code[4];
  char name[24];
  char team[24];
  uint16_t colour565;
  uint8_t grid;
  uint8_t finalPosition;
  bool secondCar;
};

struct CarSample {
  uint16_t x;
  uint16_t y;
  uint8_t state;
  bool noData;
  bool estimated;
  uint8_t position;
  uint8_t lap;
  uint16_t gap;
};

struct FrameInfo {
  uint8_t trackStatus;
  uint8_t leaderLap;
};

struct OutlinePoint {
  uint16_t x;
  uint16_t y;
};

struct LapTime {
  uint32_t endMs;
  uint32_t durationMs;
  uint8_t lap;
  uint8_t flags;
};

class RaceFile {
 public:
  RaceError open(ByteSource* source);
  void close();

  const RaceHeader& header() const { return header_; }
  const DriverInfo& driver(int index) const { return drivers_[index]; }
  const OutlinePoint* outline() const { return outline_; }
  uint32_t durationMs() const;

  // Copies a string into dst. Returns false if it does not exist. For repeated
  // strings (notes), `occurrence` picks which one.
  bool readString(uint8_t id, char* dst, uint32_t dstSize, int occurrence = 0);

  // Reads frame `index` and the one after it, so the caller can interpolate.
  bool loadFramePair(uint32_t index);
  FrameInfo frameInfo(int slot) const;
  CarSample car(int slot, int driverIndex) const;
  uint32_t loadedFrame() const { return loadedIndex_; }

  // First frame in which the leader is on `lap` or later.
  bool findLeaderLapFrame(uint8_t lap, uint32_t* frame);

  // Fills `out` with one driver's lap times. Returns how many.
  int loadDriverLaps(int driverIndex, LapTime* out, int maxLaps);

 private:
  RaceError parseHeader(const uint8_t* raw);
  bool readAt(uint32_t offset, void* dst, uint32_t count);

  ByteSource* source_ = nullptr;
  RaceHeader header_ = {};
  DriverInfo drivers_[MAX_DRIVERS];
  OutlinePoint outline_[MAX_OUTLINE_POINTS];
  uint8_t frames_[2][MAX_FRAME_SIZE];
  uint32_t loadedIndex_ = UINT32_MAX;
};
