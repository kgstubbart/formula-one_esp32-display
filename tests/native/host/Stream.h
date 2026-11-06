// Stub of the Arduino Stream class.
#pragma once

#include "Print.h"

class Stream : public Print {
 public:
  virtual int available() { return 0; }
  virtual int read() { return -1; }
  virtual int peek() { return -1; }
  virtual size_t readBytes(uint8_t*, size_t) { return 0; }
  void setTimeout(unsigned long) {}
};
