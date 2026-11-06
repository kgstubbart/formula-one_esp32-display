#include <Arduino.h>
#include <stdio.h>

#include "board_config.h"
#include "display.h"
#include "settings.h"
#include "touch_input.h"
#include "ui.h"

namespace {

constexpr int TARGET_SAMPLES = 24;

void drawTarget(int16_t x, int16_t y, uint16_t colour) {
  lcd.drawLine(x - 10, y, x + 10, y, colour);
  lcd.drawLine(x, y - 10, x, y + 10, colour);
  lcd.drawCircle(x, y, 6, colour);
}

// Waits for a steady press and returns its average raw position.
void captureRaw(uint16_t* rawX, uint16_t* rawY) {
  for (;;) {
    uint16_t x = 0;
    uint16_t y = 0;
    uint32_t sumX = 0;
    uint32_t sumY = 0;
    int count = 0;
    while (count < TARGET_SAMPLES) {
      if (touchReadRaw(&x, &y)) {
        sumX += x;
        sumY += y;
        count++;
      } else {
        count = 0;
        sumX = 0;
        sumY = 0;
      }
      delay(8);
    }
    *rawX = sumX / TARGET_SAMPLES;
    *rawY = sumY / TARGET_SAMPLES;
    uint32_t released = 0;
    while (released < 15) {  // Wait for a clean release.
      released = touchReadRaw(&x, &y) ? 0 : released + 1;
      delay(8);
    }
    return;
  }
}

void restartToApply() {
  settingsSaveDisplay();
  delay(100);
  ESP.restart();
}

void drawTestPage() {
  uiClear();
  uiTitle("Display setup");
  constexpr int BAR_W = 76;
  const uint16_t bars[4] = {colours::rgb(255, 0, 0), colours::rgb(0, 255, 0),
                            colours::rgb(0, 0, 255), colours::rgb(255, 255, 255)};
  const char* names[4] = {"RED", "GREEN", "BLUE", "WHITE"};
  lcd.setFont(&fonts::Font0);
  for (int i = 0; i < 4; i++) {
    const int x = 6 + i * (BAR_W + 2);
    lcd.fillRect(x, 30, BAR_W, 46, bars[i]);
    lcd.setTextColor(colours::TEXT, colours::BACKGROUND);
    lcd.drawString(names[i], x + 2, 80);
  }
  lcd.drawString("Bars must match their labels, background black.", 10, 96);
  char line[48];
  snprintf(line, sizeof(line), "Panel: %s  Swap R/B: %s", settingsPanelName(),
           gDisplaySettings.swapRedBlue ? "yes" : "no");
  lcd.drawString(line, 10, 108);
  snprintf(line, sizeof(line), "Rotation: %u", unsigned(gDisplaySettings.rotation));
  lcd.drawString(line, 10, 120);
}

}  // namespace

void uiCalibrateTouch() {
  const int16_t targets[3][2] = {{30, 30}, {290, 40}, {160, 210}};
  for (;;) {
    uint16_t raw[3][2];
    for (int i = 0; i < 3; i++) {
      uiClear();
      uiCentred("Touch calibration", 90, colours::TEXT, true);
      uiCentred("Press and hold each cross with a stylus", 112, colours::DIM, false);
      drawTarget(targets[i][0], targets[i][1], colours::YELLOW);
      captureRaw(&raw[i][0], &raw[i][1]);
      Serial.printf("Touch target %d raw %u %u\n", i, raw[i][0], raw[i][1]);
    }
    if (!touchCalibrationSolve(targets, raw, &gTouchCalibration)) continue;

    // Prove it works: the user has to hit a real button to accept.
    uiClear();
    uiCentred("Tap around to test, then tap Accept", 40, colours::TEXT, false);
    const Button accept = {60, 90, 200, 44, "Accept", colours::GOOD};
    const Button redo = {60, 150, 200, 44, "Calibrate again", colours::PANEL_LIGHT};
    uiButton(accept);
    uiButton(redo);
    for (;;) {
      int16_t x = 0;
      int16_t y = 0;
      if (!uiWaitTap(&x, &y, 30000)) break;  // Buttons out of reach: start again.
      lcd.fillCircle(x, y, 2, colours::YELLOW);
      if (uiHit(accept, x, y)) {
        settingsSaveTouch();
        return;
      }
      if (uiHit(redo, x, y)) break;
    }
  }
}

void uiDisplaySetup() {
  drawTestPage();
  const Button panel = {6, 136, 150, 44, "Next panel", colours::PANEL_LIGHT};
  const Button swap = {164, 136, 150, 44, "Swap red/blue", colours::PANEL_LIGHT};
  const Button rotate = {6, 186, 96, 48, "Rotate", colours::PANEL_LIGHT};
  const Button mirror = {108, 186, 96, 48, "Mirror", colours::PANEL_LIGHT};
  const Button done = {210, 186, 104, 48, "Done", colours::GOOD};
  uiButton(panel);
  uiButton(swap);
  uiButton(rotate);
  uiButton(mirror);
  uiButton(done);

  for (;;) {
    int16_t x = 0;
    int16_t y = 0;
    uiWaitTap(&x, &y);
    DisplaySettings& s = gDisplaySettings;
    if (uiHit(done, x, y)) {
      s.confirmed = true;
      settingsSaveDisplay();
      return;
    }
    s.confirmed = false;  // Come back to this page after the restart.
    if (uiHit(panel, x, y)) {
      settingsNextPanelPreset();
      restartToApply();
    } else if (uiHit(swap, x, y)) {
      s.swapRedBlue = !s.swapRedBlue;
      restartToApply();
    } else if (uiHit(rotate, x, y) || uiHit(mirror, x, y)) {
      s.rotation = uiHit(rotate, x, y) ? (s.rotation ^ 2) : (s.rotation ^ 4);
      gTouchCalibration.valid = false;  // The touch map changes with the screen.
      settingsSaveTouch();
      restartToApply();
    }
  }
}

void uiSettings() {
  for (;;) {
    uiClear();
    uiTitle("Settings");
    char brightness[24];
    snprintf(brightness, sizeof(brightness), "Brightness %u%%",
             unsigned(gDisplaySettings.brightness) * 100 / 255);
    const Button light = {10, 34, 300, 38, brightness, colours::PANEL_LIGHT};
    const Button calibrate = {10, 78, 300, 38, "Calibrate touch", colours::PANEL_LIGHT};
    const Button display = {10, 122, 300, 38, "Display setup", colours::PANEL_LIGHT};
    const Button back = {10, 196, 300, 36, "Back", colours::ACCENT};
    uiButton(light);
    uiButton(calibrate);
    uiButton(display);
    uiButton(back);
    lcd.setFont(&fonts::Font0);
    lcd.setTextColor(colours::DIM, colours::BACKGROUND);
    lcd.drawString("Data: OpenF1, Jolpica F1. Unofficial, personal use.", 10, 172);

    int16_t x = 0;
    int16_t y = 0;
    uiWaitTap(&x, &y);
    if (uiHit(back, x, y)) return;
    if (uiHit(light, x, y)) {
      DisplaySettings& s = gDisplaySettings;
      s.brightness = s.brightness >= 240 ? 60 : (s.brightness + 60 > 255 ? 255 : s.brightness + 60);
      lcd.setBrightness(s.brightness);
      settingsSaveDisplay();
    } else if (uiHit(calibrate, x, y)) {
      uiCalibrateTouch();
    } else if (uiHit(display, x, y)) {
      uiDisplaySetup();
    }
  }
}
