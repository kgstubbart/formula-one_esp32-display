// Fetches race packages from the PC's `f1tool.py serve` over Wi-Fi.
//
// The ESP32 cannot talk to OpenF1 directly: a race is about 60 MB of JSON, and the
// conversion needs far more memory than it has. So the PC does the conversion, and
// this copies the finished files, avoiding a trip with the microSD card.
#pragma once

// Runs the whole sync with progress and error screens. Restarts the board when it
// succeeds, and returns after showing an error when it does not.
void wifiSyncRun();
