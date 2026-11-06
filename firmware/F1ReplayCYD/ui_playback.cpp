// The replay screen, in landscape:
//
//   +----------------------------------------------+
//   | race name      lap     clock           badge |  y 0-17
//   +---------------------------------+------------+
//   |                                 | running    |
//   |  track map (one 16-bit sprite)  | order      |  y 18-205
//   |                                 |            |
//   +---------------------------------+------------+
//   | menu  |<  play  >|  speed |  scrubber        |  y 206-239
//   +----------------------------------------------+
//
// The sprite is the only large buffer (84 KB). Frames are read from the card two at a
// time, so RAM use does not depend on the length of the race.
#include <Arduino.h>
#include <stdio.h>
#include <string.h>

#include "board_config.h"
#include "display.h"
#include "playback.h"
#include "touch_input.h"
#include "ui.h"

namespace {

constexpr int16_t TOP_H = 18;
constexpr int16_t MAP_W = 224;
constexpr int16_t MAP_H = 188;
constexpr int16_t LIST_X = MAP_W;
constexpr int16_t LIST_W = SCREEN_W - MAP_W;
constexpr int16_t ROW_H = 9;
constexpr int ROWS_VISIBLE = 20;
constexpr int16_t BAR_Y = TOP_H + MAP_H;
constexpr int16_t BAR_H = SCREEN_H - BAR_Y;
constexpr int16_t BUTTON_W = 34;
constexpr int16_t SCRUB_X = BUTTON_W * 5 + 4;
constexpr int16_t SCRUB_W = SCREEN_W - SCRUB_X - 4;
constexpr int16_t BADGE_W = 62;
constexpr int16_t DOT_R = 3;
constexpr uint32_t FRAME_INTERVAL_MS = 40;

enum LabelMode : uint8_t { LABELS_NONE, LABELS_TOP5, LABELS_ALL, LABEL_MODE_COUNT };

struct RowCache {
  int8_t driver;
  bool selected;
  char gap[8];
};

struct Replay {
  RaceFile* race;
  Playback clock;
  LGFX_Sprite* map;
  int16_t outlineX[MAX_OUTLINE_POINTS];
  int16_t outlineY[MAX_OUTLINE_POINTS];
  float scale;
  float offsetX;
  float offsetY;
  int8_t byPosition[MAX_DRIVERS];
  CarSample cars[MAX_DRIVERS];
  uint16_t drawX[MAX_DRIVERS];
  uint16_t drawY[MAX_DRIVERS];
  RowCache rows[ROWS_VISIBLE];
  LapTime selectedLaps[MAX_LAPS_PER_DRIVER];
  int selectedLapCount;
  int selected;
  int listOffset;
  uint8_t labelMode;
  uint8_t trackStatus;
  uint8_t leaderLap;
  int16_t scrubPixel;
  char clockText[12];
  char lapText[12];
};

Replay* R = nullptr;

int16_t screenX(uint16_t x) { return int16_t(R->offsetX + x * R->scale); }
int16_t screenY(uint16_t y) { return int16_t(R->offsetY + y * R->scale); }

void prepareOutline() {
  const RaceHeader& h = R->race->header();
  constexpr float MARGIN = 6.0f;
  const float sx = (MAP_W - 2 * MARGIN) / h.mapW;
  const float sy = (MAP_H - 2 * MARGIN) / h.mapH;
  R->scale = sx < sy ? sx : sy;
  R->offsetX = (MAP_W - h.mapW * R->scale) / 2;
  R->offsetY = (MAP_H - h.mapH * R->scale) / 2;
  const OutlinePoint* points = R->race->outline();
  for (int i = 0; i < h.outlinePoints; i++) {
    R->outlineX[i] = screenX(points[i].x);
    R->outlineY[i] = screenY(points[i].y);
  }
}

uint16_t trackColour() {
  switch (R->trackStatus) {
    case TRACK_YELLOW:
    case TRACK_SC:
    case TRACK_VSC:
      return colours::YELLOW;
    case TRACK_RED:
      return colours::RED;
    case TRACK_SLOW_INFERRED:
      return colours::rgb(190, 160, 60);
    default:
      return colours::TRACK;
  }
}

const char* trackBanner() {
  switch (R->trackStatus) {
    case TRACK_YELLOW: return "YELLOW";
    case TRACK_SC: return "SAFETY CAR";
    case TRACK_VSC: return "VSC";
    case TRACK_RED: return "RED FLAG";
    case TRACK_SLOW_INFERRED: return "SLOW*";
    default: return nullptr;
  }
}

void drawLabel(LGFX_Sprite& map, const char* text, int16_t x, int16_t y, uint16_t colour) {
  // Keep labels on the map: flip to the left of the dot near the right edge.
  const int16_t width = int16_t(strlen(text)) * 6;
  const int16_t labelX = x + 5 + width > MAP_W ? x - 5 - width : x + 5;
  map.setTextColor(colour);
  map.drawString(text, labelX, y - 4);
}

void drawCar(LGFX_Sprite& map, int index) {
  const CarSample& car = R->cars[index];
  if (car.state == CAR_OUT) return;
  const DriverInfo& driver = R->race->driver(index);
  const int16_t x = screenX(R->drawX[index]);
  const int16_t y = screenY(R->drawY[index]);
  const bool selected = index == R->selected;

  if (car.noData) {
    map.drawCircle(x, y, DOT_R, colours::DIM);  // Hollow: no location for this moment.
  } else if (car.state == CAR_PIT || car.state == CAR_FINISHED) {
    map.fillCircle(x, y, DOT_R - 1, driver.colour565);
  } else {
    map.fillCircle(x, y, DOT_R, driver.colour565);
    if (driver.secondCar) map.drawCircle(x, y, DOT_R, colours::TEXT);
  }
  if (selected) map.drawCircle(x, y, DOT_R + 2, colours::TEXT);

  // Finished cars park together, where their labels would only pile up.
  const bool wanted = R->labelMode == LABELS_ALL ||
                      (R->labelMode == LABELS_TOP5 && car.position <= 5);
  const bool label = selected || (wanted && car.state != CAR_FINISHED);
  if (label) drawLabel(map, driver.code, x, y, colours::TEXT);
}

void drawSelectedLine(LGFX_Sprite& map, uint32_t raceMs) {
  const int index = R->selected;
  if (index < 0) return;
  const DriverInfo& driver = R->race->driver(index);
  const CarSample& car = R->cars[index];
  char lastLap[14] = "--";
  for (int i = R->selectedLapCount - 1; i >= 0; i--) {
    if (R->selectedLaps[i].endMs <= raceMs) {
      formatLapTime(R->selectedLaps[i].durationMs, lastLap, sizeof(lastLap));
      break;
    }
  }
  char line[40];
  snprintf(line, sizeof(line), "#%u %s P%u L%u %s", unsigned(driver.number), driver.code,
           unsigned(car.position), unsigned(car.lap), lastLap);
  map.fillRect(0, MAP_H - 10, MAP_W, 10, colours::PANEL);
  map.setTextColor(colours::TEXT);
  map.drawString(line, 2, MAP_H - 9);
}

void renderMap(uint32_t raceMs) {
  LGFX_Sprite& map = *R->map;
  const RaceHeader& h = R->race->header();
  map.fillScreen(colours::BACKGROUND);
  map.setFont(&fonts::Font0);
  map.setTextSize(1);
  map.setTextDatum(textdatum_t::top_left);

  const uint16_t colour = trackColour();
  const int points = h.outlinePoints;
  for (int i = 0; i < points; i++) {
    const int next = (i + 1) % points;
    map.drawLine(R->outlineX[i], R->outlineY[i], R->outlineX[next], R->outlineY[next], colour);
    map.drawLine(R->outlineX[i] + 1, R->outlineY[i], R->outlineX[next] + 1, R->outlineY[next],
                 colour);
  }
  map.fillRect(R->outlineX[0] - 1, R->outlineY[0] - 1, 4, 4, colours::TEXT);  // Start line.

  map.setTextColor(colours::DIM);
  map.drawString(h.fidelity == FIDELITY_TELEMETRY ? "Data: OpenF1" : "Data: Jolpica F1", 2, 2);
  if (h.flags & RACE_FLAG_SCHEMATIC) map.drawString("SCHEMATIC TRACK", 2, 12);
  if (h.flags & RACE_FLAG_LAYOUT_DIFFERS) map.drawString("LAYOUT DIFFERS", 2, 12);

  const char* banner = trackBanner();
  if (banner != nullptr) {
    map.setTextDatum(textdatum_t::top_right);
    map.setTextColor(colour);
    map.drawString(banner, MAP_W - 2, 2);
    map.setTextDatum(textdatum_t::top_left);
  }

  // Back to front, so the leader is drawn on top.
  for (int position = h.driverCount - 1; position >= 0; position--) {
    const int index = R->byPosition[position];
    if (index >= 0) drawCar(map, index);
  }
  drawSelectedLine(map, raceMs);
  map.pushSprite(&lcd, 0, TOP_H);
}

void drawRow(int row, int driverIndex) {
  const int16_t y = TOP_H + row * ROW_H;
  RowCache& cache = R->rows[row];
  char gap[8] = "";
  if (driverIndex >= 0) formatGap(R->cars[driverIndex], gap, sizeof(gap));
  const bool selected = driverIndex >= 0 && driverIndex == R->selected;
  if (cache.driver == driverIndex && cache.selected == selected && strcmp(cache.gap, gap) == 0) {
    return;
  }
  cache.driver = int8_t(driverIndex);
  cache.selected = selected;
  strncpy(cache.gap, gap, sizeof(cache.gap) - 1);

  const uint16_t background = selected ? colours::PANEL_LIGHT : colours::BACKGROUND;
  lcd.fillRect(LIST_X, y, LIST_W, ROW_H, background);
  if (driverIndex < 0) return;
  const DriverInfo& driver = R->race->driver(driverIndex);
  const CarSample& car = R->cars[driverIndex];
  const bool out = car.state == CAR_OUT;

  char position[4];
  snprintf(position, sizeof(position), "%2u", unsigned(car.position));
  lcd.setFont(&fonts::Font0);
  lcd.setTextDatum(textdatum_t::top_left);
  lcd.setTextColor(out ? colours::DIM : colours::TEXT, background);
  lcd.drawString(position, LIST_X + 1, y + 1);
  lcd.fillRect(LIST_X + 14, y + 1, 4, ROW_H - 2, out ? colours::PANEL_LIGHT : driver.colour565);
  lcd.drawString(driver.code, LIST_X + 20, y + 1);
  lcd.setTextDatum(textdatum_t::top_right);
  lcd.setTextColor(car.state == CAR_PIT ? colours::YELLOW : colours::DIM, background);
  lcd.drawString(gap, SCREEN_W - 1, y + 1);
  lcd.setTextDatum(textdatum_t::top_left);
}

void renderList() {
  const int count = R->race->header().driverCount;
  const bool paged = count > ROWS_VISIBLE;
  const int carRows = paged ? ROWS_VISIBLE - 1 : ROWS_VISIBLE;
  for (int row = 0; row < carRows; row++) {
    const int position = row + R->listOffset;
    drawRow(row, position < count ? R->byPosition[position] : -1);
  }
  if (paged) {
    const int16_t y = TOP_H + carRows * ROW_H;
    lcd.fillRect(LIST_X, y, LIST_W, ROW_H, colours::PANEL);
    lcd.setFont(&fonts::Font0);
    lcd.setTextColor(colours::TEXT, colours::PANEL);
    lcd.setTextDatum(textdatum_t::top_center);
    lcd.drawString(R->listOffset == 0 ? "more v" : "top ^", LIST_X + LIST_W / 2, y + 1);
    lcd.setTextDatum(textdatum_t::top_left);
  }
}

void renderTopBar(int32_t raceMs) {
  char lap[12];
  snprintf(lap, sizeof(lap), "L%u/%u", unsigned(R->leaderLap),
           unsigned(R->race->header().totalLaps));
  char clock[12];
  formatRaceClock(raceMs, clock, sizeof(clock));
  lcd.setFont(&fonts::Font0);
  lcd.setTextDatum(textdatum_t::middle_left);
  if (strcmp(lap, R->lapText) != 0) {
    strcpy(R->lapText, lap);
    lcd.fillRect(150, 0, 48, TOP_H, colours::PANEL);
    lcd.setTextColor(colours::TEXT, colours::PANEL);
    lcd.drawString(lap, 150, TOP_H / 2);
  }
  if (strcmp(clock, R->clockText) != 0) {
    strcpy(R->clockText, clock);
    lcd.fillRect(200, 0, 56, TOP_H, colours::PANEL);
    lcd.setTextColor(colours::TEXT, colours::PANEL);
    lcd.drawString(clock, 200, TOP_H / 2);
  }
  lcd.setTextDatum(textdatum_t::top_left);
}

void drawStaticTopBar() {
  const RaceHeader& h = R->race->header();
  char name[56];
  R->race->readString(STR_RACE, name, sizeof(name));
  char* suffix = strstr(name, "Grand Prix");
  if (suffix != nullptr) strcpy(suffix, "GP");
  char title[64];
  snprintf(title, sizeof(title), "%u %s", unsigned(h.season), name);

  lcd.fillRect(0, 0, SCREEN_W, TOP_H, colours::PANEL);
  lcd.setFont(&fonts::Font0);
  lcd.setTextColor(colours::TEXT, colours::PANEL);
  lcd.setTextDatum(textdatum_t::middle_left);
  lcd.setClipRect(0, 0, 146, TOP_H);
  lcd.drawString(title, 3, TOP_H / 2);
  lcd.clearClipRect();

  const bool estimated = h.fidelity == FIDELITY_ESTIMATED;
  const uint16_t colour = estimated ? colours::ESTIMATED : colours::APPROX;
  lcd.fillRect(SCREEN_W - BADGE_W, 0, BADGE_W, TOP_H, colour);
  lcd.setTextColor(colours::BACKGROUND, colour);
  lcd.setTextDatum(textdatum_t::middle_center);
  lcd.drawString(estimated ? "ESTIMATED" : "APPROX", SCREEN_W - BADGE_W / 2, TOP_H / 2);
  lcd.setTextDatum(textdatum_t::top_left);
}

void drawIconButton(int slot, void (*icon)(int16_t cx, int16_t cy)) {
  const int16_t x = slot * BUTTON_W;
  lcd.fillRect(x, BAR_Y, BUTTON_W, BAR_H, colours::PANEL);
  lcd.drawRect(x, BAR_Y, BUTTON_W, BAR_H, colours::BACKGROUND);
  icon(x + BUTTON_W / 2, BAR_Y + BAR_H / 2);
}

void iconMenu(int16_t cx, int16_t cy) {
  for (int i = -1; i <= 1; i++) lcd.fillRect(cx - 7, cy + i * 5 - 1, 14, 2, colours::TEXT);
}

void iconPreviousLap(int16_t cx, int16_t cy) {
  lcd.fillRect(cx - 8, cy - 7, 3, 14, colours::TEXT);
  lcd.fillTriangle(cx + 7, cy - 7, cx + 7, cy + 7, cx - 4, cy, colours::TEXT);
}

void iconNextLap(int16_t cx, int16_t cy) {
  lcd.fillRect(cx + 5, cy - 7, 3, 14, colours::TEXT);
  lcd.fillTriangle(cx - 7, cy - 7, cx - 7, cy + 7, cx + 4, cy, colours::TEXT);
}

void iconPlay(int16_t cx, int16_t cy) {
  lcd.fillTriangle(cx - 6, cy - 8, cx - 6, cy + 8, cx + 8, cy, colours::GOOD);
}

void iconPause(int16_t cx, int16_t cy) {
  lcd.fillRect(cx - 6, cy - 8, 4, 16, colours::TEXT);
  lcd.fillRect(cx + 2, cy - 8, 4, 16, colours::TEXT);
}

void iconSpeed(int16_t cx, int16_t cy) {
  char text[6];
  snprintf(text, sizeof(text), "%ux", unsigned(R->clock.speed()));
  lcd.setFont(&fonts::Font2);
  lcd.setTextColor(colours::YELLOW, colours::PANEL);
  lcd.setTextDatum(textdatum_t::middle_center);
  lcd.drawString(text, cx, cy);
  lcd.setTextDatum(textdatum_t::top_left);
}

void drawPlayButton() { drawIconButton(2, R->clock.playing() ? iconPause : iconPlay); }

void drawButtons() {
  drawIconButton(0, iconMenu);
  drawIconButton(1, iconPreviousLap);
  drawPlayButton();
  drawIconButton(3, iconNextLap);
  drawIconButton(4, iconSpeed);
}

void renderScrubber(bool force) {
  const uint32_t duration = R->clock.duration();
  const int16_t pixel = int16_t(uint64_t(R->clock.position()) * (SCRUB_W - 1) / duration);
  if (pixel == R->scrubPixel && !force) return;
  R->scrubPixel = pixel;
  const int16_t y = BAR_Y + BAR_H / 2 - 4;
  lcd.fillRect(SCRUB_X - 3, BAR_Y, SCRUB_W + 6, BAR_H, colours::BACKGROUND);
  lcd.fillRect(SCRUB_X, y, SCRUB_W, 8, colours::PANEL_LIGHT);
  lcd.fillRect(SCRUB_X, y, pixel, 8, colours::ACCENT);
  lcd.fillRect(SCRUB_X + pixel - 2, y - 6, 5, 20, colours::TEXT);
}

// Loads the frames for the current clock position and works out what to draw.
bool loadPosition() {
  RaceFile& race = *R->race;
  const RaceHeader& h = race.header();
  const uint32_t position = R->clock.position();
  if (!race.loadFramePair(position / h.frameDtMs)) return false;
  const uint8_t blend = frameBlend(position, h.frameDtMs);
  memset(R->byPosition, -1, sizeof(R->byPosition));
  for (int i = 0; i < h.driverCount; i++) {
    const CarSample from = race.car(0, i);
    const CarSample to = race.car(1, i);
    R->cars[i] = from;
    interpolateCar(from, to, blend, &R->drawX[i], &R->drawY[i]);
    if (from.position >= 1 && from.position <= h.driverCount) {
      R->byPosition[from.position - 1] = int8_t(i);
    }
  }
  const FrameInfo info = race.frameInfo(0);
  R->trackStatus = info.trackStatus;
  R->leaderLap = info.leaderLap;
  return true;
}

bool render() {
  if (!loadPosition()) return false;
  const int32_t raceMs = int32_t(R->clock.position()) - int32_t(R->race->header().lightsOutMs);
  renderMap(raceMs > 0 ? uint32_t(raceMs) : 0);
  renderList();
  renderTopBar(raceMs);
  renderScrubber(false);
  return true;
}

void selectDriver(int index) {
  R->selected = index == R->selected ? -1 : index;
  R->selectedLapCount = 0;
  if (R->selected >= 0) {
    R->selectedLapCount =
        R->race->loadDriverLaps(R->selected, R->selectedLaps, MAX_LAPS_PER_DRIVER);
  }
}

void jumpLap(int direction) {
  const RaceHeader& h = R->race->header();
  int target = int(R->leaderLap) + direction;
  if (target < 1) target = 1;
  if (target > h.totalLaps) target = h.totalLaps;
  uint32_t frame = 0;
  if (R->race->findLeaderLapFrame(uint8_t(target), &frame)) {
    R->clock.seek(frame * h.frameDtMs);
  }
}

void seekToPixel(int16_t x) {
  int32_t pixel = x - SCRUB_X;
  if (pixel < 0) pixel = 0;
  if (pixel >= SCRUB_W) pixel = SCRUB_W - 1;
  R->clock.seek(uint32_t(uint64_t(R->clock.duration()) * pixel / (SCRUB_W - 1)));
}

void drawAll() {
  uiClear();
  drawStaticTopBar();
  R->lapText[0] = 0;
  R->clockText[0] = 0;
  for (int i = 0; i < ROWS_VISIBLE; i++) {
    R->rows[i].driver = -2;
    R->rows[i].gap[0] = 0;
  }
  drawButtons();
  renderScrubber(true);
}

enum class TapResult { None, Redraw, Exit, Info };

TapResult handleTap(int16_t x, int16_t y) {
  if (y >= BAR_Y) {
    if (x >= BUTTON_W * 5) return TapResult::None;
    switch (x / BUTTON_W) {
      case 0: return TapResult::Exit;
      case 1: jumpLap(-1); break;
      case 2:
        R->clock.togglePlaying();
        drawPlayButton();
        break;
      case 3: jumpLap(1); break;
      default:
        R->clock.cycleSpeed();
        drawIconButton(4, iconSpeed);
        break;
    }
    return TapResult::Redraw;
  }
  if (y < TOP_H) return x >= SCREEN_W - BADGE_W ? TapResult::Info : TapResult::None;
  if (x >= LIST_X) {
    const int count = R->race->header().driverCount;
    const bool paged = count > ROWS_VISIBLE;
    const int carRows = paged ? ROWS_VISIBLE - 1 : ROWS_VISIBLE;
    const int row = (y - TOP_H) / ROW_H;
    if (paged && row >= carRows) {
      R->listOffset = R->listOffset == 0 ? count - carRows : 0;
      for (int i = 0; i < ROWS_VISIBLE; i++) R->rows[i].driver = -2;
    } else if (row + R->listOffset < count) {
      const int index = R->byPosition[row + R->listOffset];
      if (index >= 0) selectDriver(index);
    }
    return TapResult::Redraw;
  }
  R->labelMode = (R->labelMode + 1) % LABEL_MODE_COUNT;
  return TapResult::Redraw;
}

}  // namespace

PlaybackResult uiPlayback(RaceFile& race) {
  Replay* replay = static_cast<Replay*>(calloc(1, sizeof(Replay)));
  if (replay == nullptr) return PlaybackResult::OutOfMemory;
  replay->clock = Playback();  // calloc leaves its member defaults unset.
  LGFX_Sprite sprite(&lcd);
  sprite.setPsram(false);
  sprite.setColorDepth(16);
  if (sprite.createSprite(MAP_W, MAP_H) == nullptr) {
    // Half the memory, with coarser colours, if the heap is fragmented.
    sprite.setColorDepth(8);
    if (sprite.createSprite(MAP_W, MAP_H) == nullptr) {
      free(replay);
      return PlaybackResult::OutOfMemory;
    }
  }
  Serial.printf("Replay: free heap %u, largest block %u, map depth %d\n",
                unsigned(ESP.getFreeHeap()), unsigned(ESP.getMaxAllocHeap()),
                int(sprite.getColorDepth()));

  R = replay;
  R->race = &race;
  R->map = &sprite;
  R->selected = -1;
  R->labelMode = LABELS_TOP5;
  R->clock.reset(race.durationMs(), 0);
  R->clock.setPlaying(true);
  prepareOutline();
  drawAll();

  PlaybackResult result = PlaybackResult::Back;
  bool dirty = true;
  bool wasDown = false;
  bool scrubbing = false;
  int16_t pressX = 0;
  int16_t pressY = 0;
  uint32_t lastRender = 0;
  bool wasPlaying = true;

  for (;;) {
    const TouchPoint touch = touchRead();
    if (touch.down) {
      if (!wasDown) {
        pressX = touch.x;
        pressY = touch.y;
        scrubbing = pressY >= BAR_Y && pressX >= BUTTON_W * 5;
      }
      if (scrubbing) {
        seekToPixel(touch.x);
        dirty = true;
      }
    } else if (wasDown && !scrubbing) {
      const TapResult tap = handleTap(pressX, pressY);
      if (tap == TapResult::Exit) break;
      if (tap == TapResult::Info) {
        const bool resume = R->clock.playing();
        R->clock.setPlaying(false);
        uiRaceInfo(race);
        R->clock.setPlaying(resume);
        drawAll();
      }
      dirty = true;
    }
    wasDown = touch.down;

    R->clock.update(millis());
    if (wasPlaying != R->clock.playing()) {
      wasPlaying = R->clock.playing();
      drawPlayButton();
    }

    const uint32_t now = millis();
    if ((R->clock.playing() || dirty) && now - lastRender >= FRAME_INTERVAL_MS) {
      lastRender = now;
      dirty = false;
      if (!render()) {
        result = PlaybackResult::ReadError;
        break;
      }
    }
    delay(2);
  }

  sprite.deleteSprite();
  free(replay);
  R = nullptr;
  return result;
}
