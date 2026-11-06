// Display setup. LovyanGFX is configured here in code, so no library files need
// editing. The panel driver is chosen at runtime because boards sold as
// ESP32-2432S028 use more than one controller.
#pragma once

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#include "settings.h"

class CydDisplay : public lgfx::LGFX_Device {
 public:
  void configure(const DisplaySettings& settings);

 private:
  lgfx::Panel_ILI9341 ili9341_;
  lgfx::Panel_ST7789 st7789_;
  lgfx::Bus_SPI bus_;
  lgfx::Light_PWM backlight_;
};

extern CydDisplay lcd;

void displayBegin();

namespace colours {
constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return uint16_t(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}
constexpr uint16_t BACKGROUND = rgb(0, 0, 0);
constexpr uint16_t PANEL = rgb(28, 28, 38);
constexpr uint16_t PANEL_LIGHT = rgb(52, 52, 66);
constexpr uint16_t TEXT = rgb(255, 255, 255);
constexpr uint16_t DIM = rgb(150, 150, 165);
constexpr uint16_t ACCENT = rgb(225, 30, 20);
constexpr uint16_t TRACK = rgb(115, 115, 130);
constexpr uint16_t APPROX = rgb(0, 190, 230);
constexpr uint16_t ESTIMATED = rgb(255, 170, 0);
constexpr uint16_t GOOD = rgb(40, 200, 90);
constexpr uint16_t YELLOW = rgb(255, 220, 0);
constexpr uint16_t RED = rgb(240, 40, 40);
}  // namespace colours
