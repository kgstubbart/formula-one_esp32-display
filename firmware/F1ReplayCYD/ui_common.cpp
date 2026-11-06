#include <Arduino.h>
#include <string.h>

#include "board_config.h"
#include "display.h"
#include "touch_input.h"
#include "ui.h"

void uiClear() {
  lcd.fillScreen(colours::BACKGROUND);
  lcd.setTextDatum(textdatum_t::top_left);
  lcd.setTextSize(1);
}

void uiTitle(const char* text) {
  lcd.fillRect(0, 0, SCREEN_W, 24, colours::ACCENT);
  lcd.setFont(&fonts::Font2);
  lcd.setTextColor(colours::TEXT, colours::ACCENT);
  lcd.setTextDatum(textdatum_t::middle_left);
  lcd.drawString(text, 8, 12);
  lcd.setTextDatum(textdatum_t::top_left);
}

void uiButton(const Button& b) {
  lcd.fillRoundRect(b.x, b.y, b.w, b.h, 4, b.colour);
  lcd.setFont(&fonts::Font2);
  lcd.setTextColor(colours::TEXT, b.colour);
  lcd.setTextDatum(textdatum_t::middle_center);
  lcd.drawString(b.label, b.x + b.w / 2, b.y + b.h / 2);
  lcd.setTextDatum(textdatum_t::top_left);
}

bool uiHit(const Button& b, int16_t x, int16_t y) {
  return x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h;
}

void uiCentred(const char* text, int16_t y, uint16_t colour, bool large) {
  if (large) {
    lcd.setFont(&fonts::Font2);
  } else {
    lcd.setFont(&fonts::Font0);
  }
  lcd.setTextColor(colour, colours::BACKGROUND);
  lcd.setTextDatum(textdatum_t::top_center);
  lcd.drawString(text, SCREEN_W / 2, y);
  lcd.setTextDatum(textdatum_t::top_left);
}

bool uiWaitTap(int16_t* x, int16_t* y, uint32_t timeoutMs) {
  const uint32_t start = millis();
  bool pressed = false;
  while (timeoutMs == 0 || millis() - start < timeoutMs) {
    const TouchPoint point = touchRead();
    if (point.down && !pressed) {
      pressed = true;
      *x = point.x;
      *y = point.y;
    } else if (!point.down && pressed) {
      return true;
    }
    delay(10);
  }
  return false;
}

void uiWrapped(const char* text, int16_t x, int16_t* y, int16_t width, int16_t maxY,
               uint16_t colour, int skip, int* line) {
  constexpr int CHAR_W = 6;
  constexpr int LINE_H = 11;
  constexpr int MAX_COLUMNS = 53;
  int columns = width / CHAR_W;
  if (columns > MAX_COLUMNS) columns = MAX_COLUMNS;
  lcd.setFont(&fonts::Font0);
  lcd.setTextColor(colour, colours::BACKGROUND);
  while (*text) {
    int length = strlen(text);
    if (length > columns) {
      length = columns;
      while (length > 0 && text[length] != ' ') length--;
      if (length == 0) length = columns;
    }
    if (*line >= skip && *y + LINE_H <= maxY) {
      char buffer[MAX_COLUMNS + 1];
      memcpy(buffer, text, length);
      buffer[length] = 0;
      lcd.drawString(buffer, x, *y);
      *y += LINE_H;
    }
    (*line)++;
    text += length;
    while (*text == ' ') text++;
  }
}

void uiError(const char* title, const char* message, const char* hint, const char* retryLabel) {
  uiClear();
  lcd.fillRect(0, 0, SCREEN_W, 24, colours::RED);
  lcd.setFont(&fonts::Font2);
  lcd.setTextColor(colours::TEXT, colours::RED);
  lcd.setTextDatum(textdatum_t::middle_left);
  lcd.drawString(title, 8, 12);
  lcd.setTextDatum(textdatum_t::top_left);

  int16_t y = 36;
  int line = 0;
  uiWrapped(message, 10, &y, 300, 190, colours::TEXT, 0, &line);
  y += 8;
  if (hint != nullptr) uiWrapped(hint, 10, &y, 300, 190, colours::DIM, 0, &line);

  const Button retry = {10, 196, 300, 36, retryLabel, colours::PANEL_LIGHT};
  uiButton(retry);
  for (;;) {
    int16_t x = 0;
    int16_t tapY = 0;
    uiWaitTap(&x, &tapY);
    if (uiHit(retry, x, tapY)) return;
  }
}

void uiMessage(const char* title, const char* message) {
  uiClear();
  uiTitle(title);
  int16_t y = 36;
  int line = 0;
  uiWrapped(message, 10, &y, 300, 190, colours::TEXT, 0, &line);
  const Button ok = {10, 196, 300, 36, "OK", colours::PANEL_LIGHT};
  uiButton(ok);
  for (;;) {
    int16_t x = 0;
    int16_t tapY = 0;
    uiWaitTap(&x, &tapY);
    if (uiHit(ok, x, tapY)) return;
  }
}

void uiProgress(const char* line1, const char* line2, int percent) {
  lcd.fillRect(0, 60, SCREEN_W, 40, colours::BACKGROUND);
  uiCentred(line1, 64, colours::TEXT, true);
  uiCentred(line2, 86, colours::DIM, false);
  lcd.drawRect(30, 120, 260, 18, colours::DIM);
  const int fill = percent < 0 ? 0 : (percent > 100 ? 256 : percent * 256 / 100);
  lcd.fillRect(32, 122, fill, 14, colours::GOOD);
  lcd.fillRect(32 + fill, 122, 256 - fill, 14, colours::BACKGROUND);
}
