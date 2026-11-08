// Runs the firmware's parsers and playback logic on a PC, against real files.
//
//   tests/native/run.ps1            (builds with `python -m ziglang c++` and runs)
//
// Usage: test_firmware <out/f1replay folder>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

#include "manifest.h"
#include "playback.h"
#include "race_file.h"
#include "touch_calibration.h"

static int failures = 0;
static int checks = 0;

#define CHECK(condition)                                               \
  do {                                                                 \
    checks++;                                                          \
    if (!(condition)) {                                                \
      failures++;                                                      \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition);      \
    }                                                                  \
  } while (0)

class MemorySource : public ByteSource {
 public:
  explicit MemorySource(std::vector<uint8_t> bytes) : bytes_(std::move(bytes)) {}
  int32_t read(void* dst, uint32_t count) override {
    if (position_ >= bytes_.size()) return 0;
    if (count > bytes_.size() - position_) count = uint32_t(bytes_.size() - position_);
    memcpy(dst, bytes_.data() + position_, count);
    position_ += count;
    return int32_t(count);
  }
  bool seek(uint32_t offset) override {
    if (offset > bytes_.size()) return false;
    position_ = offset;
    return true;
  }
  uint32_t size() override { return uint32_t(bytes_.size()); }
  std::vector<uint8_t>& bytes() { return bytes_; }

 private:
  std::vector<uint8_t> bytes_;
  uint32_t position_ = 0;
};

static std::vector<uint8_t> readFile(const std::string& path) {
  FILE* file = fopen(path.c_str(), "rb");
  if (file == nullptr) {
    printf("Cannot open %s\n", path.c_str());
    exit(2);
  }
  fseek(file, 0, SEEK_END);
  const long size = ftell(file);
  fseek(file, 0, SEEK_SET);
  std::vector<uint8_t> bytes(size);
  if (fread(bytes.data(), 1, size, file) != size_t(size)) exit(2);
  fclose(file);
  return bytes;
}

static RaceFile race;  // Large, as on the device, so it does not live on the stack.

static void testRace(const std::string& path) {
  MemorySource source(readFile(path));
  CHECK(race.open(&source) == RaceError::None);
  const RaceHeader& h = race.header();
  CHECK(h.frameStride == 4 + 8 * h.driverCount);
  CHECK(h.fileSize == source.size());
  CHECK(h.mapW <= 4095 && h.mapH <= 4095);

  char text[128];
  CHECK(race.readString(STR_RACE, text, sizeof(text)));
  printf("%s: %u cars, %u frames, %u laps, fidelity %u\n", text, h.driverCount, h.frameCount,
         h.totalLaps, h.fidelity);
  for (int i = 0; race.readString(STR_NOTE, text, sizeof(text), i); i++) CHECK(strlen(text) > 0);
  CHECK(!race.readString(99, text, sizeof(text)));

  // Every frame is reachable, and positions are a permutation with x, y in range.
  for (uint32_t frame = 0; frame < h.frameCount; frame += 97) {
    CHECK(race.loadFramePair(frame));
    uint32_t seen = 0;
    for (int i = 0; i < h.driverCount; i++) {
      const CarSample car = race.car(0, i);
      CHECK(car.position >= 1 && car.position <= h.driverCount);
      seen |= 1u << (car.position - 1);
      CHECK(car.x <= 4095 && car.y <= 4095);
    }
    CHECK(seen == (h.driverCount == 32 ? 0xFFFFFFFFu : (1u << h.driverCount) - 1));
  }

  // Stepping forward, stepping back and jumping must all give the same bytes.
  CHECK(race.loadFramePair(100));
  const CarSample direct = race.car(0, 0);
  const CarSample directNext = race.car(1, 0);
  CHECK(race.loadFramePair(101));
  CHECK(race.car(0, 0).x == directNext.x);
  CHECK(race.loadFramePair(100));
  CHECK(race.car(0, 0).x == direct.x && race.car(1, 0).x == directNext.x);

  // The last frame has no successor: it must load and repeat, never read past the end.
  CHECK(race.loadFramePair(h.frameCount - 1));
  CHECK(race.car(0, 0).x == race.car(1, 0).x);
  CHECK(race.loadFramePair(h.frameCount + 500));

  // Lap search: the frame found is the first with that leader lap.
  uint32_t frame = 0;
  CHECK(race.findLeaderLapFrame(10, &frame));
  CHECK(race.loadFramePair(frame));
  CHECK(race.frameInfo(0).leaderLap >= 10);
  CHECK(race.loadFramePair(frame - 1));
  CHECK(race.frameInfo(0).leaderLap < 10);
  CHECK(!race.findLeaderLapFrame(250, &frame));

  // Lap table agrees with the race length.
  static LapTime laps[MAX_LAPS_PER_DRIVER];
  const int count = race.loadDriverLaps(0, laps, MAX_LAPS_PER_DRIVER);
  CHECK(count > 0);
  for (int i = 1; i < count; i++) CHECK(laps[i].endMs > laps[i - 1].endMs);
  CHECK(laps[count - 1].endMs <= race.durationMs());
  char time[16];
  formatLapTime(laps[count - 1].durationMs, time, sizeof(time));
  printf("  winner %s, last lap %s, %d laps\n", race.driver(0).code, time, count);
  // A lap can be absent from the source, which the race notes then report.
  CHECK(count <= h.totalLaps && count >= h.totalLaps - 3);
}

static void testDamagedRaces(const std::string& path) {
  const std::vector<uint8_t> good = readFile(path);
  {
    MemorySource source(std::vector<uint8_t>(good.begin(), good.end() - 1));
    CHECK(race.open(&source) == RaceError::WrongSize);
  }
  {
    MemorySource source(std::vector<uint8_t>(good.begin(), good.begin() + 40));
    CHECK(race.open(&source) == RaceError::NotARaceFile);
  }
  {
    MemorySource source(good);
    source.bytes()[0] = 'X';
    CHECK(race.open(&source) == RaceError::NotARaceFile);
  }
  {
    MemorySource source(good);
    source.bytes()[4] = 9;
    CHECK(race.open(&source) == RaceError::WrongVersion);
  }
  {
    MemorySource source(good);
    source.bytes()[9] ^= 1;  // Driver count, with the CRC left unchanged.
    CHECK(race.open(&source) == RaceError::BadChecksum);
  }
  {
    MemorySource source(std::vector<uint8_t>{});
    CHECK(race.open(&source) != RaceError::None);
  }
}

static void testManifest(const std::string& path) {
  MemorySource source(readFile(path));
  Manifest manifest;
  CHECK(manifest.open(&source) == ManifestError::None);
  uint16_t seasons[MAX_SEASONS];
  const int seasonCount = manifest.collectSeasons(seasons, MAX_SEASONS);
  CHECK(seasonCount > 0);
  for (int i = 1; i < seasonCount; i++) CHECK(seasons[i] < seasons[i - 1]);
  static RaceListItem items[MAX_RACES_PER_SEASON];
  int ready = 0;
  for (int i = 0; i < seasonCount; i++) {
    const int count = manifest.collectRaces(seasons[i], items, MAX_RACES_PER_SEASON);
    CHECK(count > 0);
    for (int j = 0; j < count; j++) {
      static ManifestRow row;
      CHECK(manifest.findRace(seasons[i], items[j].round, &row));
      if (row.status == 'R') {
        ready++;
        CHECK(row.size > 0 && strlen(row.file) > 4);
      }
    }
  }
  printf("Manifest: %d seasons, %d ready races\n", seasonCount, ready);

  // Auto-play order: the next ready race, wrapping round to the oldest.
  static ManifestRow next;
  if (ready > 1) {
    uint16_t lastSeason = 0;
    uint8_t lastRound = 0;
    manifest.rewind();
    while (manifest.nextRow(&next)) {
      if (next.status == 'R') {
        lastSeason = next.season;
        lastRound = next.round;
      }
    }
    CHECK(manifest.findNextReady(lastSeason, lastRound, &next));
    CHECK(next.status == 'R' && next.season <= lastSeason);
    const uint16_t firstSeason = next.season;
    const uint8_t firstRound = next.round;
    CHECK(manifest.findNextReady(firstSeason, firstRound, &next));
    CHECK(next.status == 'R' && (next.season > firstSeason ||
                                 (next.season == firstSeason && next.round > firstRound)));
  }

  static const char bad[] = "not a manifest\n";
  MemorySource badSource(std::vector<uint8_t>(bad, bad + sizeof(bad) - 1));
  CHECK(manifest.open(&badSource) == ManifestError::NotAManifest);
  static const char future[] = "F1RM\t2\tx\n";
  MemorySource futureSource(std::vector<uint8_t>(future, future + sizeof(future) - 1));
  CHECK(manifest.open(&futureSource) == ManifestError::WrongVersion);
  MemorySource empty(std::vector<uint8_t>{});
  CHECK(manifest.open(&empty) == ManifestError::Empty);

  // CRLF endings (Notepad) and malformed rows must not break browsing.
  static const char crlf[] =
      "F1RM\t1\tnow\r\n"
      "broken line\r\n"
      "2024\t1\tR\tT\ta.f1r\t10\tff\tName\tCircuit\t2024-03-02\tNote\r\n";
  MemorySource crlfSource(std::vector<uint8_t>(crlf, crlf + sizeof(crlf) - 1));
  CHECK(manifest.open(&crlfSource) == ManifestError::None);
  static ManifestRow row;
  manifest.rewind();
  CHECK(manifest.nextRow(&row));
  CHECK(row.season == 2024 && row.status == 'R' && row.crc32 == 0xFF);
  CHECK(strcmp(row.note, "Note") == 0);
  CHECK(!manifest.nextRow(&row));
}

static void testPlayback() {
  Playback playback;
  playback.reset(10000, 0);
  CHECK(!playback.playing());
  playback.update(5000);
  CHECK(playback.position() == 0);

  playback.setPlaying(true);
  playback.update(1000);  // The first update only starts the clock.
  CHECK(playback.position() == 0);
  playback.update(2000);
  CHECK(playback.position() == 2000);  // Default speed is 2x.

  playback.cycleSpeed();
  CHECK(playback.speed() == 5);
  playback.update(3000);
  CHECK(playback.position() == 7000);
  playback.update(9000);
  CHECK(playback.position() == 10000 && !playback.playing() && playback.atEnd());

  playback.togglePlaying();  // Play at the end restarts from the beginning.
  CHECK(playback.position() == 0 && playback.playing());
  playback.seek(99999);
  CHECK(playback.position() == 10000);

  // millis() wraps after 49.7 days.
  playback.reset(100000, 0);
  playback.setPlaying(true);
  playback.update(0xFFFFFF00u);
  playback.update(0x00000100u);
  CHECK(playback.position() == 512u * playback.speed());

  const uint8_t startSpeed = playback.speed();
  for (int i = 0; i < SPEED_COUNT; i++) playback.cycleSpeed();
  CHECK(playback.speed() == startSpeed);

  CHECK(frameBlend(0, 500) == 0);
  CHECK(frameBlend(250, 500) == 128);
  CHECK(frameBlend(750, 500) == 128);

  CarSample a = {};
  CarSample b = {};
  uint16_t x = 0;
  uint16_t y = 0;
  a.x = 100;
  a.y = 200;
  b.x = 200;
  b.y = 100;
  interpolateCar(a, b, 128, &x, &y);
  CHECK(x == 150 && y == 150);
  b.state = CAR_PIT;
  interpolateCar(a, b, 128, &x, &y);
  CHECK(x == 100 && y == 200);
  b.state = CAR_RUNNING;
  b.noData = true;
  interpolateCar(a, b, 128, &x, &y);
  CHECK(x == 100);
  b.noData = false;
  b.x = 3000;
  interpolateCar(a, b, 128, &x, &y);
  CHECK(x == 100);

  char text[16];
  CarSample car = {};
  car.position = 1;
  formatGap(car, text, sizeof(text));
  CHECK(strcmp(text, "LEAD") == 0);
  car.position = 2;
  car.gap = 123;
  formatGap(car, text, sizeof(text));
  CHECK(strcmp(text, "+12.3") == 0);
  car.gap = 845;
  formatGap(car, text, sizeof(text));
  CHECK(strcmp(text, "+1:24") == 0);
  car.gap = GAP_LAPS_BASE + 2;
  formatGap(car, text, sizeof(text));
  CHECK(strcmp(text, "+2L") == 0);
  car.gap = GAP_UNKNOWN;
  formatGap(car, text, sizeof(text));
  CHECK(strcmp(text, "--") == 0);
  car.state = CAR_OUT;
  formatGap(car, text, sizeof(text));
  CHECK(strcmp(text, "OUT") == 0);

  formatRaceClock(-5000, text, sizeof(text));
  CHECK(strcmp(text, "-0:05") == 0);
  formatRaceClock(754000, text, sizeof(text));
  CHECK(strcmp(text, "12:34") == 0);
  formatRaceClock(3753000, text, sizeof(text));
  CHECK(strcmp(text, "1:02:33") == 0);
  formatLapTime(96204, text, sizeof(text));
  CHECK(strcmp(text, "1:36.204") == 0);
  CHECK(crc32Update(0, "123456789", 9) == 0xCBF43926u);
}

static void testTouchCalibration() {
  const int16_t screen[3][2] = {{30, 30}, {290, 40}, {160, 210}};
  // A typical panel: axes swapped and one mirrored, as the rotated display reports them.
  const uint16_t raw[3][2] = {{3600, 3700}, {3500, 400}, {500, 2000}};
  TouchCalibration calibration = {};
  CHECK(touchCalibrationSolve(screen, raw, &calibration));
  for (int i = 0; i < 3; i++) {
    int16_t x = 0;
    int16_t y = 0;
    touchCalibrationApply(calibration, raw[i][0], raw[i][1], &x, &y);
    CHECK(abs(x - screen[i][0]) <= 1 && abs(y - screen[i][1]) <= 1);
  }
  // Three presses in almost the same spot must be refused, not produce nonsense.
  const uint16_t same[3][2] = {{2000, 2000}, {2001, 2002}, {2002, 2003}};
  TouchCalibration rejected = {};
  CHECK(!touchCalibrationSolve(screen, same, &rejected));
  CHECK(!rejected.valid);
}

int main(int argc, char** argv) {
  if (argc < 2) {
    printf("Usage: test_firmware <f1replay folder> <race file names...>\n");
    return 2;
  }
  const std::string root = argv[1];
  testPlayback();
  testTouchCalibration();
  testManifest(root + "/manifest.txt");
  for (int i = 2; i < argc; i++) testRace(root + "/races/" + argv[i]);
  if (argc > 2) testDamagedRaces(root + "/races/" + argv[2]);
  printf("%d checks, %d failures\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
