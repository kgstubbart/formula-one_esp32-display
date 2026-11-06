// Three-point touch calibration. An affine map absorbs any swap, mirror, offset or
// scale between the touch film and the screen, so one routine handles every board
// variant with no per-board constants. No hardware access, so it is tested on a PC.
#pragma once

#include <math.h>
#include <stdint.h>

struct TouchCalibration {
  bool valid;
  float m[6];  // x = m0*rx + m1*ry + m2;  y = m3*rx + m4*ry + m5
};

inline bool touchCalibrationSolve(const int16_t screen[3][2], const uint16_t raw[3][2],
                                  TouchCalibration* out) {
  const float x0 = raw[0][0], y0 = raw[0][1];
  const float x1 = raw[1][0], y1 = raw[1][1];
  const float x2 = raw[2][0], y2 = raw[2][1];
  const float det = (x0 - x2) * (y1 - y2) - (x1 - x2) * (y0 - y2);
  // Points that are nearly in a line mean the touches were not real.
  if (fabsf(det) < 10000.0f) return false;
  for (int axis = 0; axis < 2; axis++) {
    const float s0 = screen[0][axis], s1 = screen[1][axis], s2 = screen[2][axis];
    const float a = ((s0 - s2) * (y1 - y2) - (s1 - s2) * (y0 - y2)) / det;
    const float b = ((x0 - x2) * (s1 - s2) - (x1 - x2) * (s0 - s2)) / det;
    const float c = s2 - a * x2 - b * y2;
    out->m[axis * 3 + 0] = a;
    out->m[axis * 3 + 1] = b;
    out->m[axis * 3 + 2] = c;
  }
  out->valid = true;
  return true;
}

inline void touchCalibrationApply(const TouchCalibration& cal, uint16_t rawX, uint16_t rawY,
                                  int16_t* x, int16_t* y) {
  *x = int16_t(lroundf(cal.m[0] * rawX + cal.m[1] * rawY + cal.m[2]));
  *y = int16_t(lroundf(cal.m[3] * rawX + cal.m[4] * rawY + cal.m[5]));
}
