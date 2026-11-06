#include "wifi_sync.h"

#include <Arduino.h>
#include <HTTPClient.h>
#include <SD.h>
#include <WiFi.h>
#include <stdio.h>
#include <string.h>

#include "manifest.h"
#include "race_format.h"
#include "sd_store.h"
#include "ui.h"

namespace {

constexpr uint32_t WIFI_TIMEOUT_MS = 20000;
constexpr uint32_t HTTP_STALL_MS = 15000;
constexpr const char* MANIFEST_NEW = "/f1replay/manifest.new";
constexpr const char* RACE_TMP = "/f1replay/races/download.tmp";
constexpr size_t CHUNK = 4096;

struct WifiConfig {
  char ssid[33];
  char password[65];
  char server[96];
};

uint8_t chunk[CHUNK];

void copyValue(char* dst, size_t dstSize, const String& value) {
  strncpy(dst, value.c_str(), dstSize - 1);
  dst[dstSize - 1] = 0;
}

bool loadConfig(WifiConfig* config) {
  memset(config, 0, sizeof(*config));
  File file = SD.open(SD_WIFI, FILE_READ);
  if (!file) return false;
  while (file.available()) {
    String line = file.readStringUntil('\n');
    line.trim();
    const int split = line.indexOf('=');
    if (split <= 0) continue;
    const String key = line.substring(0, split);
    const String value = line.substring(split + 1);
    if (key == "ssid") copyValue(config->ssid, sizeof(config->ssid), value);
    if (key == "password") copyValue(config->password, sizeof(config->password), value);
    if (key == "server") copyValue(config->server, sizeof(config->server), value);
  }
  file.close();
  return config->ssid[0] != 0 && config->server[0] != 0;
}

bool connect(const WifiConfig& config) {
  uiProgress("Connecting to Wi-Fi", config.ssid, 0);
  WiFi.mode(WIFI_STA);
  WiFi.begin(config.ssid, config.password);
  const uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_TIMEOUT_MS) return false;
    delay(200);
  }
  return true;
}

void stopWifi() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);  // Gives its memory back for the replay's map buffer.
}

enum class Fetch { Ok, Unreachable, Interrupted, CardFull, BadChecksum };

// Streams a URL to a file on the card. expectedCrc of 0 skips the check.
Fetch download(const char* url, const char* path, uint32_t expectedSize, uint32_t expectedCrc,
               const char* label, const char* detail) {
  WiFiClient client;
  HTTPClient http;
  http.setTimeout(HTTP_STALL_MS);
  if (!http.begin(client, url)) return Fetch::Unreachable;
  if (http.GET() != HTTP_CODE_OK) {
    http.end();
    return Fetch::Unreachable;
  }
  const int total = http.getSize();
  if (expectedSize != 0 && total > 0 && uint32_t(total) != expectedSize) {
    http.end();
    return Fetch::Interrupted;
  }

  SD.remove(path);
  File file = SD.open(path, FILE_WRITE);
  if (!file) {
    http.end();
    return Fetch::CardFull;
  }
  WiFiClient* stream = http.getStreamPtr();
  uint32_t received = 0;
  uint32_t crc = 0;
  uint32_t lastData = millis();
  int lastPercent = -1;
  Fetch result = Fetch::Ok;
  while (total < 0 ? http.connected() : received < uint32_t(total)) {
    const size_t available = stream->available();
    if (available == 0) {
      if (millis() - lastData > HTTP_STALL_MS) {
        result = Fetch::Interrupted;
        break;
      }
      delay(2);
      continue;
    }
    const int got = stream->readBytes(chunk, available < CHUNK ? available : CHUNK);
    if (got <= 0) continue;
    if (file.write(chunk, got) != size_t(got)) {
      result = Fetch::CardFull;
      break;
    }
    crc = crc32Update(crc, chunk, got);
    received += got;
    lastData = millis();
    const int percent = total > 0 ? int(uint64_t(received) * 100 / uint32_t(total)) : 0;
    if (percent != lastPercent) {
      lastPercent = percent;
      uiProgress(label, detail, percent);
    }
  }
  file.close();
  http.end();
  if (result == Fetch::Ok && expectedCrc != 0 && crc != expectedCrc) result = Fetch::BadChecksum;
  if (result == Fetch::Ok && expectedSize != 0 && received != expectedSize) {
    result = Fetch::Interrupted;
  }
  if (result != Fetch::Ok) SD.remove(path);
  return result;
}

bool localCopyMatches(const char* path, uint32_t size, uint32_t crc) {
  File file = SD.open(path, FILE_READ);
  if (!file) return false;
  bool same = file.size() == size;
  uint32_t value = 0;
  while (same && file.available()) {
    const int got = file.read(chunk, CHUNK);
    if (got <= 0) break;
    value = crc32Update(value, chunk, got);
  }
  file.close();
  return same && value == crc;
}

void showFetchError(Fetch result, const char* server) {
  char message[200];
  switch (result) {
    case Fetch::Unreachable:
      snprintf(message, sizeof(message), "Could not reach the race server at %s.", server);
      uiError("PC not reachable", message,
              "On the PC run: python f1tool.py serve. Keep that window open. Check that the "
              "address in wifi.txt matches and that Windows Firewall allows it.",
              "Back");
      break;
    case Fetch::CardFull:
      uiError("Card write failed", "The file could not be written to the microSD card.",
              "The card may be full, locked or damaged.", "Back");
      break;
    case Fetch::BadChecksum:
      uiError("Download damaged", "The file failed its checksum and was discarded.",
              "Run Sync again.", "Back");
      break;
    default:
      uiError("Download interrupted", "The connection dropped during the download.",
              "Move the board closer to your router and run Sync again.", "Back");
      break;
  }
}

}  // namespace

void wifiSyncRun() {
  static WifiConfig config;
  if (!loadConfig(&config)) {
    uiError("No Wi-Fi settings", "wifi.txt is missing or incomplete on the microSD card.",
            "On the PC run: python f1tool.py wifi, then copy the f1replay folder to the card. "
            "Wi-Fi sync is optional: you can also copy races by hand.",
            "Back");
    return;
  }
  uiClear();
  uiTitle("Wi-Fi sync");
  SD.mkdir(SD_ROOT);  // A blank card has no folders yet.
  SD.mkdir(SD_RACES);
  if (!connect(config)) {
    stopWifi();
    char message[96];
    snprintf(message, sizeof(message), "Could not join the network \"%s\".", config.ssid);
    uiError("No Wi-Fi", message,
            "Check the name and password in wifi.txt. The ESP32 only works with 2.4 GHz "
            "networks. You can still copy races to the card by hand.",
            "Back");
    return;
  }

  static char url[192];
  snprintf(url, sizeof(url), "%s%s", config.server, SD_MANIFEST);
  Fetch result = download(url, MANIFEST_NEW, 0, 0, "Fetching catalog", config.server);
  if (result != Fetch::Ok) {
    stopWifi();
    showFetchError(result, config.server);
    return;
  }

  static SdSource source;
  static Manifest manifest;
  static ManifestRow row;
  if (!source.open(MANIFEST_NEW) || manifest.open(&source) != ManifestError::None) {
    source.close();
    SD.remove(MANIFEST_NEW);
    stopWifi();
    uiError("Bad catalog", "The server sent a catalog this firmware cannot read.",
            "Update the firmware and the data tool together.", "Back");
    return;
  }

  int total = 0;
  manifest.rewind();
  while (manifest.nextRow(&row)) total += row.status == 'R' && sdRaceFileNameOk(row.file);

  int done = 0;
  int fetched = 0;
  manifest.rewind();
  while (manifest.nextRow(&row)) {
    if (row.status != 'R' || !sdRaceFileNameOk(row.file)) continue;
    done++;
    char label[40];
    snprintf(label, sizeof(label), "Race %d of %d", done, total);
    uiProgress(label, row.name, 0);
    char path[64];
    sdRacePath(row.file, path, sizeof(path));
    if (localCopyMatches(path, row.size, row.crc32)) continue;

    snprintf(url, sizeof(url), "%s%s/%s", config.server, SD_RACES, row.file);
    result = download(url, RACE_TMP, row.size, row.crc32, label, row.name);
    if (result != Fetch::Ok) {
      source.close();
      SD.remove(MANIFEST_NEW);
      stopWifi();
      showFetchError(result, config.server);
      return;
    }
    SD.remove(path);
    SD.rename(RACE_TMP, path);
    fetched++;
  }

  // Swap the catalog last, so a failed sync leaves the old one in place.
  source.close();
  SD.remove(SD_MANIFEST);
  SD.rename(MANIFEST_NEW, SD_MANIFEST);
  stopWifi();

  char summary[64];
  snprintf(summary, sizeof(summary), "%d race(s) downloaded, %d already up to date.", fetched,
           total - fetched);
  uiMessage("Sync complete", summary);
  ESP.restart();  // Starts playback with all of the memory Wi-Fi was using.
}
