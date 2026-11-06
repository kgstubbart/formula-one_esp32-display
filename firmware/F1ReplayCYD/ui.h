// Screens. Each one blocks until the user makes a choice, then returns it.
#pragma once

#include <stdint.h>

#include "manifest.h"
#include "race_file.h"

struct Button {
  int16_t x;
  int16_t y;
  int16_t w;
  int16_t h;
  const char* label;
  uint16_t colour;
};

// Shared drawing helpers (ui_common.cpp).
void uiClear();
void uiTitle(const char* text);
void uiButton(const Button& button);
bool uiHit(const Button& button, int16_t x, int16_t y);
void uiCentred(const char* text, int16_t y, uint16_t colour, bool large);
bool uiWaitTap(int16_t* x, int16_t* y, uint32_t timeoutMs = 0);

// Draws word-wrapped text from line `skip` on, moving *y down. *line counts every
// line, including ones that are skipped or fall below maxY.
void uiWrapped(const char* text, int16_t x, int16_t* y, int16_t width, int16_t maxY,
               uint16_t colour, int skip, int* line);

// Shows an error and waits for the button. The caller decides what happens next.
void uiError(const char* title, const char* message, const char* hint, const char* retryLabel);
void uiMessage(const char* title, const char* message);
void uiProgress(const char* line1, const char* line2, int percent);

// Setup screens (ui_setup.cpp).
void uiCalibrateTouch();
void uiDisplaySetup();
void uiSettings();

// Browsing (ui_menu.cpp).
constexpr int MENU_BACK = -1;
constexpr int MENU_SETTINGS = -2;
constexpr int MENU_SYNC = -3;

// Returns an index into `seasons`, or MENU_SETTINGS / MENU_SYNC.
int uiChooseSeason(const uint16_t* seasons, int count);
// Returns an index into `races`, or MENU_BACK.
int uiChooseRace(uint16_t season, const RaceListItem* races, int count);
// Shows why a race cannot be played.
void uiRaceUnavailable(const ManifestRow& row);
// Returns true to start the replay, false to go back.
bool uiRaceInfo(RaceFile& race);

// Replay (ui_playback.cpp). Returns when the user leaves.
enum class PlaybackResult { Back, OutOfMemory, ReadError };
PlaybackResult uiPlayback(RaceFile& race);
