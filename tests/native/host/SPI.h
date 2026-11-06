// Stub: the PC preview draws into memory, so no SPI bus exists.
#pragma once

#include <stdint.h>

#define SPI_MODE0 0
#define SPI_MODE1 1
#define SPI_MODE2 2
#define SPI_MODE3 3
#define MSBFIRST 1

struct SPISettings {
  SPISettings() {}
  SPISettings(uint32_t, uint8_t, uint8_t) {}
};

class SPIClass {
 public:
  void begin() {}
  void end() {}
  void beginTransaction(SPISettings) {}
  void endTransaction() {}
  uint8_t transfer(uint8_t value) { return value; }
  uint16_t transfer16(uint16_t value) { return value; }
  void transfer(void*, uint32_t) {}
};

extern SPIClass SPI;
