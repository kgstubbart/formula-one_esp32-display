// The replay clock and frame interpolation. No hardware access, so it can be
// tested on a PC.
#pragma once

#include <stdint.h>

#include "race_file.h"

constexpr int SPEED_COUNT = 6;
extern const uint8_t PLAYBACK_SPEEDS[SPEED_COUNT];

class Playback {
 public:
  void reset(uint32_t durationMs, uint32_t startMs);

  // Advances the clock. `nowMs` is the monotonic time (millis()).
  void update(uint32_t nowMs);

  void togglePlaying();
  void setPlaying(bool playing);
  void cycleSpeed();
  void setSpeedIndex(uint8_t index) { speedIndex_ = index < SPEED_COUNT ? index : speedIndex_; }
  uint8_t speedIndex() const { return speedIndex_; }
  void seek(uint32_t positionMs);

  bool playing() const { return playing_; }
  uint8_t speed() const { return PLAYBACK_SPEEDS[speedIndex_]; }
  uint32_t position() const { return position_; }
  uint32_t duration() const { return duration_; }
  bool atEnd() const { return position_ >= duration_; }

 private:
  uint32_t duration_ = 0;
  uint32_t position_ = 0;
  uint32_t lastUpdate_ = 0;
  uint8_t speedIndex_ = 1;
  bool playing_ = false;
  bool clockValid_ = false;
};

// Position within a frame pair, 0-255.
uint8_t frameBlend(uint32_t positionMs, uint32_t frameDtMs);

// Blends one car between its two surrounding frames. Cars stay put across a state
// change or a large jump, so a dot never sweeps across the infield.
void interpolateCar(const CarSample& from, const CarSample& to, uint8_t blend, uint16_t* x,
                    uint16_t* y);

// Writes the gap column of the running order, such as "+12.3", "+1L", "PIT" or "OUT".
void formatGap(const CarSample& car, char* dst, int dstSize);

// Writes the race clock relative to lights-out, as "-0:05", "12:34" or "1:02:33".
void formatRaceClock(int32_t raceMs, char* dst, int dstSize);

// Writes a lap time, as "1:36.204".
void formatLapTime(uint32_t ms, char* dst, int dstSize);
