// Pin map for the ESP32-2432S028 ("Cheap Yellow Display").
//
// Source: witnessmenow/ESP32-Cheap-Yellow-Display, PINS.md.
// The display has the HSPI bus and the SD card has VSPI. The ESP32 has no third
// free SPI bus, so touch is bit-banged in software (see touch_input.cpp).
#pragma once

// Display (HSPI)
constexpr int PIN_TFT_SCLK = 14;
constexpr int PIN_TFT_MOSI = 13;
constexpr int PIN_TFT_MISO = 12;
constexpr int PIN_TFT_DC = 2;
constexpr int PIN_TFT_CS = 15;
constexpr int PIN_TFT_BACKLIGHT = 21;

// Touch controller XPT2046 (software SPI)
constexpr int PIN_TOUCH_CLK = 25;
constexpr int PIN_TOUCH_MOSI = 32;
constexpr int PIN_TOUCH_MISO = 39;
constexpr int PIN_TOUCH_CS = 33;

// microSD (VSPI)
constexpr int PIN_SD_SCK = 18;
constexpr int PIN_SD_MISO = 19;
constexpr int PIN_SD_MOSI = 23;
constexpr int PIN_SD_CS = 5;

// RGB LED, active low. Switched off at boot.
constexpr int PIN_LED_RED = 4;
constexpr int PIN_LED_GREEN = 16;
constexpr int PIN_LED_BLUE = 17;

// Held at power-on to move to the next panel preset.
constexpr int PIN_BOOT_BUTTON = 0;

constexpr int SCREEN_W = 320;
constexpr int SCREEN_H = 240;

constexpr unsigned long SD_SPI_HZ = 16000000UL;
constexpr unsigned long TFT_WRITE_HZ = 40000000UL;
constexpr unsigned long TFT_READ_HZ = 16000000UL;
