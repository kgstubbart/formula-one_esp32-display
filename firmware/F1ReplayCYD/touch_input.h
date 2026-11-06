// Resistive touch input from the XPT2046.
#pragma once

#include <stdint.h>

struct TouchPoint {
  int16_t x;
  int16_t y;
  bool down;
};

void touchBegin();

// Reads the raw controller values. Returns false when nothing is pressing the screen.
bool touchReadRaw(uint16_t* x, uint16_t* y);

// Returns the debounced touch state in screen coordinates. Needs a calibration.
TouchPoint touchRead();
