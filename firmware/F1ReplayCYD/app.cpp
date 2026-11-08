#include "app.h"

#include <Arduino.h>
#include <stdio.h>

#include "board_config.h"
#include "display.h"
#include "manifest.h"
#include "race_file.h"
#include "sd_store.h"
#include "settings.h"
#include "touch_input.h"
#include "ui.h"
#include "wifi_sync.h"

namespace {

SdSource manifestSource;
SdSource raceSource;
Manifest manifest;
RaceFile race;
ManifestRow selectedRow;
ManifestRow nextRow;
uint8_t speedIndex = 1;  // Kept between races, so auto-play continues at the same speed.
uint16_t seasons[MAX_SEASONS];
RaceListItem races[MAX_RACES_PER_SEASON];
int seasonCount = 0;

void waitForCard() {
  while (!sdBegin()) {
    uiError("No microSD card", "The microSD card was not found or could not be read.",
            "Power off, insert a FAT32 card (32 GB or smaller is safest), then press Retry.",
            "Retry");
  }
}

void openManifest() {
  for (;;) {
    waitForCard();
    if (manifestSource.open(SD_MANIFEST)) {
      const ManifestError error = manifest.open(&manifestSource);
      if (error == ManifestError::None) {
        seasonCount = manifest.collectSeasons(seasons, MAX_SEASONS);
        if (seasonCount > 0) return;
        uiError("No races on card", "The race catalog on the card is empty.",
                "On the PC, build a race and copy the f1replay folder to the card.", "Retry");
      } else if (error == ManifestError::WrongVersion) {
        uiError("Update needed", "The catalog was made by a different version of the data tool.",
                "Use the data tool and firmware from the same release.", "Retry");
      } else {
        uiError("Bad catalog", "/f1replay/manifest.txt is damaged or is not a race catalog.",
                "Copy the f1replay folder to the card again.", "Retry");
      }
      manifestSource.close();
    } else {
      // With Wi-Fi settings on the card, a first sync can fill an otherwise empty card.
      SdSource wifiFile;
      const bool canSync = wifiFile.open(SD_WIFI);
      wifiFile.close();
      uiError("No race files", "/f1replay/manifest.txt was not found on the card.",
              "On the PC run: python f1tool.py build, then copy the f1replay folder to the "
              "root of the card. See the README.",
              canSync ? "Sync over Wi-Fi" : "Retry");
      if (canSync) wifiSyncRun();
    }
  }
}

// Opens and checks one race. Shows the error and returns false if it cannot be played.
bool openRace(const ManifestRow& row) {
  char path[64];
  if (!sdRaceFileNameOk(row.file)) {
    uiError("Bad race entry", "The catalog has an invalid file name for this race.",
            "Rebuild the manifest with the data tool.", "Back");
    return false;
  }
  sdRacePath(row.file, path, sizeof(path));
  if (!raceSource.open(path)) {
    char message[120];
    snprintf(message, sizeof(message), "%s is in the catalog but its file is not on the card.",
             row.file);
    uiError("Race file missing", message, "Copy the f1replay folder to the card again.", "Back");
    return false;
  }
  const RaceError error = race.open(&raceSource);
  if (error != RaceError::None) {
    raceSource.close();
    uiError("Bad race file", raceErrorText(error),
            "Rebuild the race with the data tool, or copy it to the card again.", "Back");
    return false;
  }
  return true;
}

// Plays a race, and then the following ones while auto-play is on.
void playRace(const ManifestRow& first) {
  static ManifestRow current;
  current = first;
  bool showInfo = true;  // Only before a race the user picked, not between auto-played ones.
  for (;;) {
    if (!openRace(current)) return;
    static char nextName[56];
    const char* next = nullptr;
    if (gAutoNext && manifest.findNextReady(current.season, current.round, &nextRow)) {
      snprintf(nextName, sizeof(nextName), "%u %s", unsigned(nextRow.season), nextRow.name);
      next = nextName;
    }
    PlaybackResult result = PlaybackResult::Back;
    if (!showInfo || uiRaceInfo(race)) result = uiPlayback(race, next, &speedIndex);
    race.close();
    raceSource.close();

    if (result == PlaybackResult::Next) {
      current = nextRow;
      showInfo = false;
      continue;
    }
    if (result == PlaybackResult::OutOfMemory) {
      uiError("Out of memory", "There is not enough free memory for the track map.",
              "Restart the board and try again.", "Back");
    } else if (result == PlaybackResult::ReadError) {
      uiError("Card read error", "Reading the race from the card failed during the replay.",
              "Check that the card is seated properly.", "Back");
    }
    return;
  }
}

void browseSeason(uint16_t season) {
  const int count = manifest.collectRaces(season, races, MAX_RACES_PER_SEASON);
  for (;;) {
    const int choice = uiChooseRace(season, races, count);
    if (choice == MENU_BACK) return;
    if (!manifest.findRace(season, races[choice].round, &selectedRow)) continue;
    if (selectedRow.status == 'R') {
      playRace(selectedRow);
    } else {
      uiRaceUnavailable(selectedRow);
    }
  }
}

}  // namespace

void appSetup() {
  Serial.begin(115200);
  for (int pin : {PIN_LED_RED, PIN_LED_GREEN, PIN_LED_BLUE}) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, HIGH);
  }
  pinMode(PIN_BOOT_BUTTON, INPUT_PULLUP);

  settingsLoad();
  if (digitalRead(PIN_BOOT_BUTTON) == LOW) {
    // The way out if the panel is unreadable: it needs no screen and no touch.
    settingsNextPanelPreset();
    gDisplaySettings.confirmed = false;
    settingsSaveDisplay();
  }
  displayBegin();
  touchBegin();
  if (!gTouchCalibration.valid) uiCalibrateTouch();
  if (!gDisplaySettings.confirmed) uiDisplaySetup();
}

void appLoop() {
  openManifest();
  for (;;) {
    const int choice = uiChooseSeason(seasons, seasonCount);
    if (choice == MENU_SETTINGS) {
      uiSettings();
    } else if (choice == MENU_SYNC) {
      manifestSource.close();
      wifiSyncRun();
      return;  // Reopen the manifest.
    } else {
      browseSeason(seasons[choice]);
    }
  }
}
