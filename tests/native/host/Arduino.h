// Minimal Arduino API so the firmware's UI files and LovyanGFX build on a PC.
#pragma once

#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "Print.h"
#include "Stream.h"

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define PROGMEM
#define memcpy_P memcpy
#define F(text) (text)
#define pgm_read_byte(p) (*(const uint8_t*)(p))
#define pgm_read_word(p) (*(const uint16_t*)(p))
#define pgm_read_dword(p) (*(const uint32_t*)(p))
#define pgm_read_ptr(p) (*(void* const*)(p))
#define constrain(v, lo, hi) ((v) < (lo) ? (lo) : ((v) > (hi) ? (hi) : (v)))

// Only what LovyanGFX's text functions need.
class String {
 public:
  String(const char* text = "") : text_(text) {}
  const char* c_str() const { return text_; }
  size_t length() const { return strlen(text_); }

 private:
  const char* text_;
};

extern uint32_t hostMillis;
inline uint32_t millis() { return hostMillis; }
inline uint32_t micros() { return hostMillis * 1000; }
inline void delay(uint32_t ms) { hostMillis += ms; }
inline void delayMicroseconds(uint32_t) {}
inline void yield() {}
#ifdef _WIN32
inline struct tm* gmtime_r(const time_t* when, struct tm* result) {
  return gmtime_s(result, when) == 0 ? result : nullptr;
}
#endif
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return 0; }

struct HostSerial {
  void printf(const char* format, ...) {
    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
  }
};
extern HostSerial Serial;

struct HostEsp {
  void restart() {
    puts("ESP.restart()");
    exit(0);
  }
  uint32_t getFreeHeap() { return 200000; }
  uint32_t getMaxAllocHeap() { return 110000; }
};
extern HostEsp ESP;
