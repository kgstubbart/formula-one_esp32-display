// Runs the real UI code on a PC with a scripted "finger", and saves what it draws.
// The LovyanGFX drawing, layout, tap handling, race parsing and playback are the same
// code as on the board. Only the display object and the touch driver are replaced.
//
// Usage: ui_preview <out dir> <f1replay dir> <race file> [more race files]
#include <Arduino.h>
#include <stdio.h>

#include <deque>
#include <string>
#include <vector>

#include "board_config.h"
#include "display.h"
#include "manifest.h"
#include "race_file.h"
#include "settings.h"
#include "touch_input.h"
#include "ui.h"

uint32_t hostMillis = 0;
HostSerial Serial;
HostEsp ESP;
SPIClass SPI;
CydDisplay lcd;
DisplaySettings gDisplaySettings = {PANEL_ILI9341, false, false, 1, 200, false};
TouchCalibration gTouchCalibration = {true, {1, 0, 0, 0, 1, 0}};

void settingsSaveDisplay() {}
void settingsSaveTouch() {}
void settingsNextPanelPreset() {}
const char* settingsPanelName() { return "ILI9341 normal"; }
void touchBegin() {}

namespace {

std::string outDir;

void screenshot(const std::string& name) {
  const std::string path = outDir + "/" + name + ".ppm";
  FILE* file = fopen(path.c_str(), "wb");
  fprintf(file, "P6\n%d %d\n255\n", SCREEN_W, SCREEN_H);
  const uint16_t* pixels = static_cast<const uint16_t*>(lcd.getBuffer());
  for (int i = 0; i < SCREEN_W * SCREEN_H; i++) {
    const uint16_t raw = pixels[i];
    const uint16_t value = uint16_t((raw >> 8) | (raw << 8));  // Sprites store big-endian.
    const uint8_t rgb[3] = {uint8_t((value >> 11) << 3), uint8_t(((value >> 5) & 63) << 2),
                            uint8_t((value & 31) << 3)};
    fwrite(rgb, 1, 3, file);
  }
  fclose(file);
}

struct Step {
  enum Kind { Wait, Tap, Drag, Shot } kind;
  int x;
  int y;
  uint32_t ms;
  std::string name;
};

std::deque<Step> script;
uint32_t waitUntil = 0;
int tapPhase = 0;

void wait(uint32_t ms) { script.push_back({Step::Wait, 0, 0, ms, ""}); }
void tap(int x, int y) { script.push_back({Step::Tap, x, y, 0, ""}); }
void drag(int x, int y) { script.push_back({Step::Drag, x, y, 0, ""}); }
void shot(const std::string& name) { script.push_back({Step::Shot, 0, 0, 0, name}); }

// Each screen blocks in a loop that polls touch, so this is what drives the program.
TouchPoint nextTouch() {
  hostMillis += 1;
  for (;;) {
    if (script.empty()) {
      puts("Script ended while a screen was still waiting: a tap missed its target.");
      exit(3);
    }
    Step& step = script.front();
    if (step.kind == Step::Shot) {
      screenshot(step.name);
      script.pop_front();
      continue;
    }
    if (step.kind == Step::Wait) {
      if (waitUntil == 0) waitUntil = hostMillis + step.ms;
      if (hostMillis < waitUntil) return {0, 0, false};
      waitUntil = 0;
      script.pop_front();
      continue;
    }
    if (tapPhase < 3) {
      tapPhase++;
      return {int16_t(step.x), int16_t(step.y), true};
    }
    const bool isDrag = step.kind == Step::Drag;
    tapPhase = 0;
    script.pop_front();
    if (isDrag) continue;
    return {int16_t(step.x), int16_t(step.y), false};
  }
}

class FileSource : public ByteSource {
 public:
  explicit FileSource(const std::string& path) { file_ = fopen(path.c_str(), "rb"); }
  int32_t read(void* dst, uint32_t count) override {
    return int32_t(fread(dst, 1, count, file_));
  }
  bool seek(uint32_t offset) override { return fseek(file_, offset, SEEK_SET) == 0; }
  uint32_t size() override {
    const long at = ftell(file_);
    fseek(file_, 0, SEEK_END);
    const long end = ftell(file_);
    fseek(file_, at, SEEK_SET);
    return uint32_t(end);
  }

 private:
  FILE* file_;
};

RaceFile race;

void replayScript(const std::string& name) {
  shot(name + "_info");
  tap(265, 217);               // Replay.
  wait(1500);
  shot(name + "_start");
  tap(102 - 34 + 17, 222);     // Pause... the play button is slot 2.
  tap(4 * 34 + 17, 222);       // Speed 2x -> 5x.
  tap(4 * 34 + 17, 222);       // 5x -> 10x.
  drag(174 + 70, 222);         // Scrub to about 45%.
  wait(300);
  shot(name + "_mid");
  tap(260, 18 + 3 * 9 + 4);    // Select the car in P4.
  tap(100, 100);               // Labels: top 5 -> all.
  wait(300);
  shot(name + "_selected");
  tap(3 * 34 + 17, 222);       // Next lap.
  tap(1 * 34 + 17, 222);       // Previous lap.
  tap(2 * 34 + 17, 222);       // Play.
  wait(500);
  drag(315, 222);              // Scrub to the end.
  wait(300);
  shot(name + "_end");
  tap(17, 222);                // Menu: leave the replay.
}

void runRace(const std::string& root, const std::string& fileName, const std::string& name) {
  FileSource source(root + "/races/" + fileName);
  const RaceError error = race.open(&source);
  if (error != RaceError::None) {
    printf("%s: %s\n", fileName.c_str(), raceErrorText(error));
    exit(2);
  }
  replayScript(name);
  if (uiRaceInfo(race)) {
    const PlaybackResult result = uiPlayback(race);
    printf("%s: replay ended with result %d\n", name.c_str(), int(result));
  } else {
    puts("Replay button missed.");
    exit(3);
  }
  race.close();
}

}  // namespace

TouchPoint touchRead() { return nextTouch(); }

bool touchReadRaw(uint16_t* x, uint16_t* y) {
  const TouchPoint point = nextTouch();
  *x = uint16_t(point.x);
  *y = uint16_t(point.y);
  return point.down;
}

int main(int argc, char** argv) {
  if (argc < 4) {
    puts("Usage: ui_preview <out dir> <f1replay dir> <race files...>");
    return 2;
  }
  outDir = argv[1];
  const std::string root = argv[2];
  lcd.setColorDepth(16);
  lcd.createSprite(SCREEN_W, SCREEN_H);

  // Catalog screens, from the real manifest.
  FileSource manifestFile(root + "/manifest.txt");
  static Manifest manifest;
  if (manifest.open(&manifestFile) != ManifestError::None) return 2;
  static uint16_t seasons[MAX_SEASONS];
  const int seasonCount = manifest.collectSeasons(seasons, MAX_SEASONS);
  shot("menu_seasons");
  tap(40, 56);
  if (uiChooseSeason(seasons, seasonCount) != 0) return 3;

  static RaceListItem races[MAX_RACES_PER_SEASON];
  const int raceCount = manifest.collectRaces(seasons[0], races, MAX_RACES_PER_SEASON);
  shot("menu_races");
  tap(150, 40);
  const int first = uiChooseRace(seasons[0], races, raceCount);
  printf("Tapped race index %d\n", first);

  static ManifestRow row;
  if (manifest.findRace(1990, 1, &row)) {
    shot("menu_unavailable");
    tap(160, 214);
    uiRaceUnavailable(row);
  }

  shot("error_sd");
  tap(160, 214);
  uiError("No microSD card", "The microSD card was not found or could not be read.",
          "Power off, insert a FAT32 card (32 GB or smaller is safest), then press Retry.",
          "Retry");

  shot("setup_settings");
  tap(160, 214);
  uiSettings();

  shot("setup_display");
  tap(260, 210);
  uiDisplaySetup();

  for (int i = 3; i < argc; i++) {
    std::string name = argv[i];
    name = "race_" + name.substr(0, name.find('.'));
    runRace(root, argv[i], name);
  }
  puts("UI script finished.");
  return 0;
}
