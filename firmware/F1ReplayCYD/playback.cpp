#include "playback.h"

#include <stdio.h>
#include <stdlib.h>

const uint8_t PLAYBACK_SPEEDS[SPEED_COUNT] = {1, 2, 5, 10, 30, 60};

namespace {
constexpr int MAX_BLEND_JUMP = 900;  // Map units; about 5 seconds of flat-out driving.
}

void Playback::reset(uint32_t durationMs, uint32_t startMs) {
  duration_ = durationMs;
  position_ = startMs < durationMs ? startMs : durationMs;
  playing_ = false;
  clockValid_ = false;
}

void Playback::update(uint32_t nowMs) {
  if (!playing_) return;
  if (!clockValid_) {
    lastUpdate_ = nowMs;
    clockValid_ = true;
    return;
  }
  const uint32_t elapsed = nowMs - lastUpdate_;
  lastUpdate_ = nowMs;
  const uint64_t next = uint64_t(position_) + uint64_t(elapsed) * speed();
  if (next >= duration_) {
    position_ = duration_;
    playing_ = false;
  } else {
    position_ = uint32_t(next);
  }
}

void Playback::setPlaying(bool playing) {
  if (playing && atEnd()) position_ = 0;
  playing_ = playing;
  clockValid_ = false;
}

void Playback::togglePlaying() { setPlaying(!playing_); }

void Playback::cycleSpeed() { speedIndex_ = (speedIndex_ + 1) % SPEED_COUNT; }

void Playback::seek(uint32_t positionMs) {
  position_ = positionMs < duration_ ? positionMs : duration_;
  clockValid_ = false;
}

uint8_t frameBlend(uint32_t positionMs, uint32_t frameDtMs) {
  return uint8_t((positionMs % frameDtMs) * 256 / frameDtMs);
}

void interpolateCar(const CarSample& from, const CarSample& to, uint8_t blend, uint16_t* x,
                    uint16_t* y) {
  const int dx = int(to.x) - int(from.x);
  const int dy = int(to.y) - int(from.y);
  if (from.state != to.state || from.noData || to.noData || abs(dx) > MAX_BLEND_JUMP ||
      abs(dy) > MAX_BLEND_JUMP) {
    *x = from.x;
    *y = from.y;
    return;
  }
  *x = uint16_t(from.x + dx * blend / 256);
  *y = uint16_t(from.y + dy * blend / 256);
}

void formatGap(const CarSample& car, char* dst, int dstSize) {
  if (car.state == CAR_OUT) {
    snprintf(dst, dstSize, "OUT");
  } else if (car.state == CAR_PIT) {
    snprintf(dst, dstSize, "PIT");
  } else if (car.state == CAR_FINISHED) {
    snprintf(dst, dstSize, "FIN");
  } else if (car.position == 1) {
    snprintf(dst, dstSize, "LEAD");
  } else if (car.gap == GAP_UNKNOWN) {
    snprintf(dst, dstSize, "--");
  } else if (car.gap >= GAP_LAPS_BASE) {
    snprintf(dst, dstSize, "+%uL", unsigned(car.gap - GAP_LAPS_BASE));
  } else if (car.gap < 600) {
    snprintf(dst, dstSize, "+%u.%u", unsigned(car.gap / 10), unsigned(car.gap % 10));
  } else {
    const unsigned seconds = car.gap / 10;
    snprintf(dst, dstSize, "+%u:%02u", seconds / 60, seconds % 60);
  }
}

void formatRaceClock(int32_t raceMs, char* dst, int dstSize) {
  const bool negative = raceMs < 0;
  const uint32_t total = uint32_t(negative ? -raceMs : raceMs) / 1000;
  const unsigned hours = total / 3600;
  const unsigned minutes = (total / 60) % 60;
  const unsigned seconds = total % 60;
  if (hours > 0) {
    snprintf(dst, dstSize, "%s%u:%02u:%02u", negative ? "-" : "", hours, minutes, seconds);
  } else {
    snprintf(dst, dstSize, "%s%u:%02u", negative ? "-" : "", minutes, seconds);
  }
}

void formatLapTime(uint32_t ms, char* dst, int dstSize) {
  snprintf(dst, dstSize, "%u:%02u.%03u", unsigned(ms / 60000), unsigned((ms / 1000) % 60),
           unsigned(ms % 1000));
}
