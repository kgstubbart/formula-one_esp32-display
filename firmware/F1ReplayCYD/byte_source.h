// Random-access input. The parsers use this instead of the SD library, which
// lets the same parser code run in the PC tests in tests/native.
#pragma once

#include <stdint.h>

class ByteSource {
 public:
  virtual ~ByteSource() {}
  // Returns the number of bytes read, or a negative number on error.
  virtual int32_t read(void* dst, uint32_t count) = 0;
  virtual bool seek(uint32_t offset) = 0;
  virtual uint32_t size() = 0;
};
