// Settings kept in flash, so the display and touch setup survive a power cycle.
#pragma once

#include <stdint.h>

#include "touch_calibration.h"

enum PanelType : uint8_t { PANEL_ILI9341 = 0, PANEL_ST7789 = 1 };

struct DisplaySettings {
  uint8_t panel;
  bool invert;
  bool swapRedBlue;
  uint8_t rotation;  // LovyanGFX rotation: 1 or 3 landscape, 5 or 7 mirrored landscape.
  uint8_t brightness;
  bool confirmed;  // The user has finished the display setup page.
};

extern DisplaySettings gDisplaySettings;
extern bool gAutoNext;  // Go on to the next race when one finishes.
extern TouchCalibration gTouchCalibration;

void settingsLoad();
void settingsSaveDisplay();
void settingsSaveTouch();
void settingsSaveAutoNext();

// Moves to the next (panel, inversion) combination.
void settingsNextPanelPreset();
const char* settingsPanelName();
