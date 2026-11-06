// Stub of the Arduino Print base class, which LovyanGFX inherits from.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

class __FlashStringHelper;

class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t) = 0;
  size_t write(const char* text) { return write(reinterpret_cast<const uint8_t*>(text), strlen(text)); }
  size_t write(const char* text, size_t size) {
    return write(reinterpret_cast<const uint8_t*>(text), size);
  }
  virtual size_t write(const uint8_t* buffer, size_t size) {
    size_t count = 0;
    while (size--) count += write(*buffer++);
    return count;
  }
};
