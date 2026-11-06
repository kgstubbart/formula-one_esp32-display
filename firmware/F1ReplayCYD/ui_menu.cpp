#include <Arduino.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "board_config.h"
#include "display.h"
#include "ui.h"

namespace {

constexpr int SEASONS_PER_PAGE = 12;
constexpr int RACES_PER_PAGE = 5;
constexpr int ROW_H = 33;
constexpr int INFO_TEXT_TOP = 30;
constexpr int INFO_TEXT_BOTTOM = 192;
constexpr int INFO_SCROLL_LINES = 10;

const Button PREVIOUS = {6, 200, 92, 34, "< Prev", colours::PANEL_LIGHT};
const Button NEXT = {222, 200, 92, 34, "Next >", colours::PANEL_LIGHT};

struct Chip {
  const char* text;
  uint16_t colour;
};

Chip chipFor(const RaceListItem& race) {
  switch (race.status) {
    case 'R':
      return race.fidelity == 'T' ? Chip{"APPROX", colours::APPROX}
                                  : Chip{"ESTIMATED", colours::ESTIMATED};
    case 'N': return Chip{"NOT ON CARD", colours::DIM};
    case 'F': return Chip{"NOT RACED", colours::DIM};
    default: return Chip{"NO DATA", colours::RED};
  }
}

void drawChip(const Chip& chip, int16_t right, int16_t y) {
  const int width = strlen(chip.text) * 6 + 8;
  const uint16_t background = chip.colour;
  lcd.fillRoundRect(right - width, y, width, 13, 3, background);
  lcd.setFont(&fonts::Font0);
  lcd.setTextColor(colours::BACKGROUND, background);
  lcd.drawString(chip.text, right - width + 4, y + 3);
}

void formatUtc(uint64_t epochMs, char* dst, int dstSize) {
  const time_t seconds = time_t(epochMs / 1000);
  struct tm moment;
  gmtime_r(&seconds, &moment);
  strftime(dst, dstSize, "%Y-%m-%d %H:%M:%S UTC", &moment);
}

}  // namespace

int uiChooseSeason(const uint16_t* seasons, int count) {
  int page = 0;
  const int pages = (count + SEASONS_PER_PAGE - 1) / SEASONS_PER_PAGE;
  for (;;) {
    uiClear();
    uiTitle("F1 Replay: choose a season");
    Button items[SEASONS_PER_PAGE];
    char labels[SEASONS_PER_PAGE][6];
    const int first = page * SEASONS_PER_PAGE;
    const int shown = count - first < SEASONS_PER_PAGE ? count - first : SEASONS_PER_PAGE;
    for (int i = 0; i < shown; i++) {
      snprintf(labels[i], sizeof(labels[i]), "%u", unsigned(seasons[first + i]));
      items[i] = {int16_t(6 + (i % 4) * 78), int16_t(32 + (i / 4) * 54), 74, 48, labels[i],
                  colours::PANEL_LIGHT};
      uiButton(items[i]);
    }
    const Button settings = {102, 200, 56, 34, "Setup", colours::PANEL};
    const Button sync = {162, 200, 56, 34, "Sync", colours::PANEL};
    uiButton(settings);
    uiButton(sync);
    if (pages > 1) {
      uiButton(PREVIOUS);
      uiButton(NEXT);
    }

    int16_t x = 0;
    int16_t y = 0;
    uiWaitTap(&x, &y);
    if (uiHit(settings, x, y)) return MENU_SETTINGS;
    if (uiHit(sync, x, y)) return MENU_SYNC;
    if (pages > 1 && uiHit(PREVIOUS, x, y)) page = (page + pages - 1) % pages;
    if (pages > 1 && uiHit(NEXT, x, y)) page = (page + 1) % pages;
    for (int i = 0; i < shown; i++) {
      if (uiHit(items[i], x, y)) return first + i;
    }
  }
}

int uiChooseRace(uint16_t season, const RaceListItem* races, int count) {
  int page = 0;
  const int pages = (count + RACES_PER_PAGE - 1) / RACES_PER_PAGE;
  const Button back = {112, 200, 96, 34, "Seasons", colours::ACCENT};
  for (;;) {
    uiClear();
    char title[32];
    snprintf(title, sizeof(title), "%u season  (%d/%d)", unsigned(season), page + 1, pages);
    uiTitle(title);
    const int first = page * RACES_PER_PAGE;
    const int shown = count - first < RACES_PER_PAGE ? count - first : RACES_PER_PAGE;
    for (int i = 0; i < shown; i++) {
      const RaceListItem& race = races[first + i];
      const int16_t y = 28 + i * ROW_H;
      lcd.fillRoundRect(4, y, SCREEN_W - 8, ROW_H - 3, 3, colours::PANEL);
      char label[48];
      snprintf(label, sizeof(label), "%2u %s", unsigned(race.round), race.name);
      lcd.setFont(&fonts::Font2);
      lcd.setTextColor(race.status == 'R' ? colours::TEXT : colours::DIM, colours::PANEL);
      lcd.setClipRect(8, y, 226, ROW_H - 3);
      lcd.drawString(label, 8, y + 7);
      lcd.clearClipRect();
      drawChip(chipFor(race), SCREEN_W - 8, y + 9);
    }
    if (pages > 1) {
      uiButton(PREVIOUS);
      uiButton(NEXT);
    }
    uiButton(back);

    int16_t x = 0;
    int16_t y = 0;
    uiWaitTap(&x, &y);
    if (uiHit(back, x, y)) return MENU_BACK;
    if (pages > 1 && uiHit(PREVIOUS, x, y)) {
      page = (page + pages - 1) % pages;
    } else if (pages > 1 && uiHit(NEXT, x, y)) {
      page = (page + 1) % pages;
    } else if (y >= 28 && y < 28 + shown * ROW_H) {
      return first + (y - 28) / ROW_H;
    }
  }
}

void uiRaceUnavailable(const ManifestRow& row) {
  char message[320];
  const char* action = "";
  if (row.status == 'N') {
    action = " Build it on your PC with f1tool, then copy it to the card or use Sync.";
  }
  snprintf(message, sizeof(message), "%s, %s. %s%s", row.circuit, row.date, row.note, action);
  uiMessage(row.name, message);
}

bool uiRaceInfo(RaceFile& race) {
  const RaceHeader& header = race.header();
  const bool estimated = header.fidelity == FIDELITY_ESTIMATED;
  const Button back = {6, 200, 92, 34, "Back", colours::ACCENT};
  const Button up = {104, 200, 50, 34, "Up", colours::PANEL_LIGHT};
  const Button down = {160, 200, 50, 34, "Down", colours::PANEL_LIGHT};
  const Button play = {216, 200, 98, 34, "Replay", colours::GOOD};
  int scroll = 0;
  char text[420];

  for (;;) {
    uiClear();
    race.readString(STR_RACE, text, sizeof(text));
    char title[56];
    snprintf(title, sizeof(title), "%u %s", unsigned(header.season), text);
    uiTitle(title);

    int16_t y = INFO_TEXT_TOP;
    int line = 0;
    uiWrapped(estimated ? "ESTIMATED POSITIONS, derived from real lap times"
                        : "APPROXIMATE POSITIONS from location data",
              8, &y, 304, INFO_TEXT_BOTTOM, estimated ? colours::ESTIMATED : colours::APPROX,
              scroll, &line);
    race.readString(STR_CIRCUIT, text, sizeof(text));
    uiWrapped(text, 8, &y, 304, INFO_TEXT_BOTTOM, colours::TEXT, scroll, &line);
    char start[40];
    formatUtc(header.startEpochMs + header.lightsOutMs, start, sizeof(start));
    snprintf(text, sizeof(text), "Lights out %s. %u laps, %u cars.", start,
             unsigned(header.totalLaps), unsigned(header.driverCount));
    uiWrapped(text, 8, &y, 304, INFO_TEXT_BOTTOM, colours::TEXT, scroll, &line);
    if (race.readString(STR_SOURCES, text, sizeof(text))) {
      char prefixed[440];
      snprintf(prefixed, sizeof(prefixed), "Data: %s", text);
      uiWrapped(prefixed, 8, &y, 304, INFO_TEXT_BOTTOM, colours::DIM, scroll, &line);
    }
    for (int i = 0; race.readString(STR_NOTE, text, sizeof(text), i); i++) {
      uiWrapped(text, 8, &y, 304, INFO_TEXT_BOTTOM, colours::TEXT, scroll, &line);
    }
    const bool canScroll = scroll > 0 || line - scroll > (INFO_TEXT_BOTTOM - INFO_TEXT_TOP) / 11;
    uiButton(back);
    uiButton(play);
    if (canScroll) {
      uiButton(up);
      uiButton(down);
    }

    int16_t x = 0;
    int16_t tapY = 0;
    uiWaitTap(&x, &tapY);
    if (uiHit(back, x, tapY)) return false;
    if (uiHit(play, x, tapY)) return true;
    if (canScroll && uiHit(up, x, tapY)) {
      scroll = scroll > INFO_SCROLL_LINES ? scroll - INFO_SCROLL_LINES : 0;
    }
    if (canScroll && uiHit(down, x, tapY) && scroll + INFO_SCROLL_LINES < line) {
      scroll += INFO_SCROLL_LINES;
    }
  }
}
