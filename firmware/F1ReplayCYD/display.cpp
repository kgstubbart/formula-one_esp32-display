#include "display.h"

#include "board_config.h"

CydDisplay lcd;

void CydDisplay::configure(const DisplaySettings& settings) {
  const bool st7789 = settings.panel == PANEL_ST7789;

  auto busConfig = bus_.config();
  busConfig.spi_host = SPI2_HOST;  // HSPI
  busConfig.spi_mode = st7789 ? 3 : 0;
  busConfig.freq_write = TFT_WRITE_HZ;
  busConfig.freq_read = TFT_READ_HZ;
  busConfig.spi_3wire = false;
  busConfig.use_lock = true;
  busConfig.dma_channel = SPI_DMA_CH_AUTO;
  busConfig.pin_sclk = PIN_TFT_SCLK;
  busConfig.pin_mosi = PIN_TFT_MOSI;
  busConfig.pin_miso = PIN_TFT_MISO;
  busConfig.pin_dc = PIN_TFT_DC;
  bus_.config(busConfig);

  lgfx::Panel_Device* panel = st7789 ? static_cast<lgfx::Panel_Device*>(&st7789_)
                                     : static_cast<lgfx::Panel_Device*>(&ili9341_);
  panel->setBus(&bus_);
  auto panelConfig = panel->config();
  panelConfig.pin_cs = PIN_TFT_CS;
  panelConfig.pin_rst = -1;
  panelConfig.pin_busy = -1;
  panelConfig.panel_width = 240;
  panelConfig.panel_height = 320;
  panelConfig.memory_width = 240;
  panelConfig.memory_height = 320;
  panelConfig.offset_x = 0;
  panelConfig.offset_y = 0;
  panelConfig.offset_rotation = 0;
  panelConfig.readable = false;
  panelConfig.invert = settings.invert;
  panelConfig.rgb_order = settings.swapRedBlue;
  panelConfig.dlen_16bit = false;
  panelConfig.bus_shared = false;
  panel->config(panelConfig);

  auto lightConfig = backlight_.config();
  lightConfig.pin_bl = PIN_TFT_BACKLIGHT;
  lightConfig.invert = false;
  lightConfig.freq = 12000;
  lightConfig.pwm_channel = 7;
  backlight_.config(lightConfig);
  panel->setLight(&backlight_);

  setPanel(panel);
}

void displayBegin() {
  lcd.configure(gDisplaySettings);
  lcd.init();
  lcd.setColorDepth(16);
  lcd.setRotation(gDisplaySettings.rotation);
  lcd.setBrightness(gDisplaySettings.brightness);
  lcd.fillScreen(colours::BACKGROUND);
}
