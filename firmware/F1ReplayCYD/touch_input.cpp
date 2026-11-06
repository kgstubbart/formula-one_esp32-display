// The XPT2046 is read by bit-banging its pins. All of the ESP32's free hardware SPI
// buses are taken: the display has HSPI and the SD card has VSPI. A touch reading is
// only a few hundred clock pulses, so software SPI costs well under a millisecond.
#include "touch_input.h"

#include <Arduino.h>

#include "board_config.h"
#include "settings.h"

namespace {

constexpr uint8_t CMD_Z1 = 0xB1;
constexpr uint8_t CMD_Z2 = 0xC1;
constexpr uint8_t CMD_X = 0xD1;
constexpr uint8_t CMD_Y = 0x91;
constexpr uint8_t CMD_POWER_DOWN = 0x90;
constexpr int PRESSURE_THRESHOLD = 400;
constexpr int SAMPLES = 5;
constexpr int PRESS_READS = 2;
constexpr int RELEASE_READS = 3;

uint16_t transfer(uint8_t command) {
  for (int bit = 7; bit >= 0; bit--) {
    digitalWrite(PIN_TOUCH_MOSI, (command >> bit) & 1);
    digitalWrite(PIN_TOUCH_CLK, HIGH);
    delayMicroseconds(1);
    digitalWrite(PIN_TOUCH_CLK, LOW);
    delayMicroseconds(1);
  }
  digitalWrite(PIN_TOUCH_MOSI, LOW);
  uint16_t value = 0;
  for (int bit = 0; bit < 16; bit++) {
    digitalWrite(PIN_TOUCH_CLK, HIGH);
    delayMicroseconds(1);
    value = uint16_t((value << 1) | digitalRead(PIN_TOUCH_MISO));
    digitalWrite(PIN_TOUCH_CLK, LOW);
    delayMicroseconds(1);
  }
  return (value >> 3) & 0x0FFF;
}

uint16_t medianRead(uint8_t command) {
  uint16_t samples[SAMPLES];
  transfer(command);  // The first conversion after switching axis is unsettled.
  for (int i = 0; i < SAMPLES; i++) {
    const uint16_t value = transfer(command);
    int j = i - 1;
    while (j >= 0 && samples[j] > value) {
      samples[j + 1] = samples[j];
      j--;
    }
    samples[j + 1] = value;
  }
  return samples[SAMPLES / 2];
}

}  // namespace

void touchBegin() {
  pinMode(PIN_TOUCH_CLK, OUTPUT);
  pinMode(PIN_TOUCH_MOSI, OUTPUT);
  pinMode(PIN_TOUCH_CS, OUTPUT);
  pinMode(PIN_TOUCH_MISO, INPUT);
  digitalWrite(PIN_TOUCH_CS, HIGH);
  digitalWrite(PIN_TOUCH_CLK, LOW);
}

bool touchReadRaw(uint16_t* x, uint16_t* y) {
  digitalWrite(PIN_TOUCH_CS, LOW);
  const int z1 = transfer(CMD_Z1);
  const int z2 = transfer(CMD_Z2);
  const int pressure = z1 + 4095 - z2;
  const bool pressed = z1 > 0 && pressure > PRESSURE_THRESHOLD;
  if (pressed) {
    *x = medianRead(CMD_X);
    *y = medianRead(CMD_Y);
  }
  transfer(CMD_POWER_DOWN);
  digitalWrite(PIN_TOUCH_CS, HIGH);
  return pressed;
}

TouchPoint touchRead() {
  static TouchPoint state = {0, 0, false};
  static int pressCount = 0;
  static int releaseCount = 0;

  uint16_t rawX = 0;
  uint16_t rawY = 0;
  if (touchReadRaw(&rawX, &rawY)) {
    releaseCount = 0;
    if (++pressCount >= PRESS_READS) {
      touchCalibrationApply(gTouchCalibration, rawX, rawY, &state.x, &state.y);
      state.x = constrain(state.x, 0, SCREEN_W - 1);
      state.y = constrain(state.y, 0, SCREEN_H - 1);
      state.down = true;
    }
  } else {
    pressCount = 0;
    if (++releaseCount >= RELEASE_READS) state.down = false;
  }
  return state;
}
