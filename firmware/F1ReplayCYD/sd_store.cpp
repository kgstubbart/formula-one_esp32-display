#include "sd_store.h"

#include <SD.h>
#include <SPI.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "board_config.h"

namespace {
SPIClass sdSpi(VSPI);
bool busStarted = false;
}  // namespace

bool sdBegin() {
  if (!busStarted) {
    sdSpi.begin(PIN_SD_SCK, PIN_SD_MISO, PIN_SD_MOSI, PIN_SD_CS);
    busStarted = true;
  }
  SD.end();
  return SD.begin(PIN_SD_CS, sdSpi, SD_SPI_HZ) && SD.cardType() != CARD_NONE;
}

bool sdRaceFileNameOk(const char* name) {
  const size_t length = strlen(name);
  if (length < 5 || length > 24 || strcmp(name + length - 4, ".f1r") != 0) return false;
  for (size_t i = 0; i < length - 4; i++) {
    if (!isdigit(uint8_t(name[i])) && name[i] != '_') return false;
  }
  return true;
}

void sdRacePath(const char* name, char* dst, int dstSize) {
  snprintf(dst, dstSize, "%s/%s", SD_RACES, name);
}

bool SdSource::open(const char* path) {
  close();
  file_ = SD.open(path, FILE_READ);
  return bool(file_);
}

void SdSource::close() {
  if (file_) file_.close();
}

int32_t SdSource::read(void* dst, uint32_t count) {
  return file_ ? int32_t(file_.read(static_cast<uint8_t*>(dst), count)) : -1;
}

bool SdSource::seek(uint32_t offset) { return file_ && file_.seek(offset); }

uint32_t SdSource::size() { return file_ ? uint32_t(file_.size()) : 0; }
