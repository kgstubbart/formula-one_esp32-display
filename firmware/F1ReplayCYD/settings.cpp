#include "settings.h"

#include <Preferences.h>

DisplaySettings gDisplaySettings;
TouchCalibration gTouchCalibration;

namespace {
constexpr const char* NAMESPACE = "f1replay";
Preferences prefs;
}  // namespace

void settingsLoad() {
  prefs.begin(NAMESPACE, true);
  gDisplaySettings.panel = prefs.getUChar("panel", PANEL_ILI9341);
  gDisplaySettings.invert = prefs.getBool("invert", false);
  gDisplaySettings.swapRedBlue = prefs.getBool("swaprb", false);
  gDisplaySettings.rotation = prefs.getUChar("rotation", 1);
  gDisplaySettings.brightness = prefs.getUChar("bright", 200);
  gDisplaySettings.confirmed = prefs.getBool("confirmed", false);
  gTouchCalibration.valid = prefs.getBytes("touchcal", gTouchCalibration.m,
                                           sizeof(gTouchCalibration.m)) ==
                            sizeof(gTouchCalibration.m);
  prefs.end();
}

void settingsSaveDisplay() {
  prefs.begin(NAMESPACE, false);
  prefs.putUChar("panel", gDisplaySettings.panel);
  prefs.putBool("invert", gDisplaySettings.invert);
  prefs.putBool("swaprb", gDisplaySettings.swapRedBlue);
  prefs.putUChar("rotation", gDisplaySettings.rotation);
  prefs.putUChar("bright", gDisplaySettings.brightness);
  prefs.putBool("confirmed", gDisplaySettings.confirmed);
  prefs.end();
}

void settingsSaveTouch() {
  prefs.begin(NAMESPACE, false);
  if (gTouchCalibration.valid) {
    prefs.putBytes("touchcal", gTouchCalibration.m, sizeof(gTouchCalibration.m));
  } else {
    prefs.remove("touchcal");
  }
  prefs.end();
}

void settingsNextPanelPreset() {
  DisplaySettings& s = gDisplaySettings;
  if (!s.invert) {
    s.invert = true;
  } else {
    s.invert = false;
    s.panel = s.panel == PANEL_ILI9341 ? PANEL_ST7789 : PANEL_ILI9341;
  }
}

const char* settingsPanelName() {
  const DisplaySettings& s = gDisplaySettings;
  if (s.panel == PANEL_ILI9341) return s.invert ? "ILI9341 inverted" : "ILI9341 normal";
  return s.invert ? "ST7789 inverted" : "ST7789 normal";
}
