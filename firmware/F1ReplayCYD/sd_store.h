// microSD access. Everything that touches the SD library lives here and in wifi_sync.
#pragma once

#include <FS.h>
#include <stdint.h>

#include "byte_source.h"

constexpr const char* SD_ROOT = "/f1replay";
constexpr const char* SD_MANIFEST = "/f1replay/manifest.txt";
constexpr const char* SD_RACES = "/f1replay/races";
constexpr const char* SD_WIFI = "/f1replay/wifi.txt";

// Mounts the card. Safe to call again after a failure.
bool sdBegin();

// Only plain "2024_01.f1r" style names, so a bad manifest cannot reach other files.
bool sdRaceFileNameOk(const char* name);
void sdRacePath(const char* name, char* dst, int dstSize);

class SdSource : public ByteSource {
 public:
  bool open(const char* path);
  void close();
  int32_t read(void* dst, uint32_t count) override;
  bool seek(uint32_t offset) override;
  uint32_t size() override;

 private:
  fs::File file_;
};
