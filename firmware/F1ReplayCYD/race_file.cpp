#include "race_file.h"

#include <string.h>

namespace {

uint16_t u16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }

uint32_t u32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

uint64_t u64(const uint8_t* p) { return uint64_t(u32(p)) | (uint64_t(u32(p + 4)) << 32); }

// A section must sit inside the file and must not run past the next one.
bool sectionFits(uint32_t offset, uint64_t length, uint32_t limit) {
  return offset >= RACE_HEADER_SIZE && uint64_t(offset) + length <= limit;
}

}  // namespace

uint32_t crc32Update(uint32_t crc, const void* data, size_t length) {
  const uint8_t* bytes = static_cast<const uint8_t*>(data);
  crc = ~crc;
  for (size_t i = 0; i < length; i++) {
    crc ^= bytes[i];
    for (int bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
  }
  return ~crc;
}

const char* raceErrorText(RaceError error) {
  switch (error) {
    case RaceError::None: return "OK";
    case RaceError::NotARaceFile: return "This is not a race file.";
    case RaceError::WrongVersion: return "The race file format does not match this firmware.";
    case RaceError::BadChecksum: return "The race file header is damaged.";
    case RaceError::WrongSize: return "The race file is incomplete or damaged.";
    case RaceError::BadLayout: return "The race file has invalid contents.";
    case RaceError::ReadFailed: return "The race file could not be read from the card.";
  }
  return "Unknown error.";
}

RaceError RaceFile::parseHeader(const uint8_t* p) {
  if (memcmp(p, "F1RP", 4) != 0) return RaceError::NotARaceFile;
  header_.version = u16(p + 4);
  if (header_.version != RACE_FORMAT_VERSION) return RaceError::WrongVersion;
  if (crc32Update(0, p, RACE_HEADER_SIZE - 4) != u32(p + RACE_HEADER_SIZE - 4)) {
    return RaceError::BadChecksum;
  }
  if (u16(p + 6) != RACE_HEADER_SIZE) return RaceError::BadLayout;

  header_.fidelity = p[8];
  header_.driverCount = p[9];
  header_.flags = u16(p + 10);
  header_.season = u16(p + 12);
  header_.round = p[14];
  header_.totalLaps = u16(p + 16);
  header_.frameDtMs = u16(p + 18);
  header_.frameCount = u32(p + 20);
  header_.lightsOutMs = u32(p + 24);
  header_.startEpochMs = u64(p + 28);
  header_.mapW = u16(p + 36);
  header_.mapH = u16(p + 38);
  header_.outlinePoints = u16(p + 40);
  header_.frameStride = u16(p + 42);
  header_.lapRecords = u32(p + 44);
  header_.stringsOffset = u32(p + 48);
  header_.stringsLength = u32(p + 52);
  header_.driversOffset = u32(p + 56);
  header_.outlineOffset = u32(p + 60);
  header_.lapsOffset = u32(p + 64);
  header_.framesOffset = u32(p + 68);
  header_.fileSize = u32(p + 72);

  if (header_.driverCount == 0 || header_.driverCount > MAX_DRIVERS) return RaceError::BadLayout;
  if (header_.outlinePoints < 3 || header_.outlinePoints > MAX_OUTLINE_POINTS) {
    return RaceError::BadLayout;
  }
  if (header_.frameDtMs == 0 || header_.frameCount < 2 || header_.mapW == 0 || header_.mapH == 0) {
    return RaceError::BadLayout;
  }
  if (header_.frameStride != RACE_FRAME_HEADER_SIZE + RACE_CAR_SIZE * header_.driverCount) {
    return RaceError::BadLayout;
  }
  if (header_.fidelity != FIDELITY_TELEMETRY && header_.fidelity != FIDELITY_ESTIMATED) {
    return RaceError::BadLayout;
  }

  const uint64_t frameBytes = uint64_t(header_.frameCount) * header_.frameStride;
  if (uint64_t(header_.framesOffset) + frameBytes != header_.fileSize) return RaceError::WrongSize;
  if (!sectionFits(header_.stringsOffset, header_.stringsLength, header_.driversOffset) ||
      !sectionFits(header_.driversOffset, uint64_t(header_.driverCount) * RACE_DRIVER_SIZE,
                   header_.outlineOffset) ||
      !sectionFits(header_.outlineOffset, uint64_t(header_.outlinePoints) * 4,
                   header_.lapsOffset) ||
      !sectionFits(header_.lapsOffset, uint64_t(header_.lapRecords) * RACE_LAP_RECORD_SIZE,
                   header_.framesOffset)) {
    return RaceError::BadLayout;
  }
  return RaceError::None;
}

RaceError RaceFile::open(ByteSource* source) {
  close();
  source_ = source;
  uint8_t raw[RACE_HEADER_SIZE];
  if (!readAt(0, raw, sizeof(raw))) {
    return source->size() < RACE_HEADER_SIZE ? RaceError::NotARaceFile : RaceError::ReadFailed;
  }
  RaceError error = parseHeader(raw);
  if (error != RaceError::None) return error;
  if (source->size() != header_.fileSize) return RaceError::WrongSize;

  for (int i = 0; i < header_.driverCount; i++) {
    uint8_t d[RACE_DRIVER_SIZE];
    if (!readAt(header_.driversOffset + i * RACE_DRIVER_SIZE, d, sizeof(d))) {
      return RaceError::ReadFailed;
    }
    DriverInfo& info = drivers_[i];
    info.number = u16(d);
    memcpy(info.code, d + 2, sizeof(info.code));
    memcpy(info.name, d + 6, sizeof(info.name));
    memcpy(info.team, d + 30, sizeof(info.team));
    info.code[sizeof(info.code) - 1] = 0;
    info.name[sizeof(info.name) - 1] = 0;
    info.team[sizeof(info.team) - 1] = 0;
    info.colour565 = u16(d + 54);
    info.grid = d[59];
    info.finalPosition = d[60];
    info.secondCar = d[61] & 1;
  }

  uint8_t pointBytes[4];
  for (int i = 0; i < header_.outlinePoints; i++) {
    if (!readAt(header_.outlineOffset + i * 4, pointBytes, 4)) return RaceError::ReadFailed;
    outline_[i].x = u16(pointBytes);
    outline_[i].y = u16(pointBytes + 2);
  }
  if (!loadFramePair(0)) return RaceError::ReadFailed;
  return RaceError::None;
}

void RaceFile::close() {
  source_ = nullptr;
  loadedIndex_ = UINT32_MAX;
  header_ = RaceHeader();
}

uint32_t RaceFile::durationMs() const {
  return (header_.frameCount - 1) * uint32_t(header_.frameDtMs);
}

bool RaceFile::readAt(uint32_t offset, void* dst, uint32_t count) {
  if (source_ == nullptr || !source_->seek(offset)) return false;
  return source_->read(dst, count) == int32_t(count);
}

bool RaceFile::readString(uint8_t id, char* dst, uint32_t dstSize, int occurrence) {
  if (dstSize == 0) return false;
  dst[0] = 0;
  uint32_t offset = header_.stringsOffset;
  const uint32_t end = offset + header_.stringsLength;
  while (offset + 3 <= end) {
    uint8_t prefix[3];
    if (!readAt(offset, prefix, sizeof(prefix))) return false;
    const uint16_t length = u16(prefix + 1);
    offset += 3;
    if (offset + length > end) return false;
    if (prefix[0] == id && occurrence-- == 0) {
      const uint32_t copy = length < dstSize - 1 ? length : dstSize - 1;
      if (!readAt(offset, dst, copy)) return false;
      dst[copy] = 0;
      return true;
    }
    offset += length;
  }
  return false;
}

bool RaceFile::loadFramePair(uint32_t index) {
  if (index >= header_.frameCount) index = header_.frameCount - 1;
  if (index == loadedIndex_) return true;
  const uint32_t stride = header_.frameStride;
  const uint32_t offset = header_.framesOffset + index * stride;
  if (index + 1 == loadedIndex_ && loadedIndex_ != UINT32_MAX) {
    // Rewinding one frame is the same data the other way around.
    memcpy(frames_[1], frames_[0], stride);
    if (!readAt(offset, frames_[0], stride)) return false;
  } else if (index == loadedIndex_ + 1 && loadedIndex_ != UINT32_MAX) {
    memcpy(frames_[0], frames_[1], stride);
    if (index + 1 < header_.frameCount && !readAt(offset + stride, frames_[1], stride)) {
      return false;
    }
  } else {
    if (!readAt(offset, frames_[0], stride)) return false;
    if (index + 1 < header_.frameCount) {
      if (source_->read(frames_[1], stride) != int32_t(stride)) return false;
    } else {
      memcpy(frames_[1], frames_[0], stride);
    }
  }
  loadedIndex_ = index;
  return true;
}

FrameInfo RaceFile::frameInfo(int slot) const {
  return FrameInfo{frames_[slot][0], frames_[slot][1]};
}

CarSample RaceFile::car(int slot, int driverIndex) const {
  const uint8_t* p = frames_[slot] + RACE_FRAME_HEADER_SIZE + driverIndex * RACE_CAR_SIZE;
  const uint16_t packed = u16(p);
  CarSample sample;
  sample.x = packed & 0x0FFF;
  sample.y = u16(p + 2) & 0x0FFF;
  sample.state = (packed >> 12) & 3;
  sample.noData = packed & (1 << 14);
  sample.estimated = packed & (1 << 15);
  sample.position = p[4];
  sample.lap = p[5];
  sample.gap = u16(p + 6);
  return sample;
}

bool RaceFile::findLeaderLapFrame(uint8_t lap, uint32_t* frame) {
  uint32_t low = 0;
  uint32_t high = header_.frameCount;
  while (low < high) {
    const uint32_t middle = low + (high - low) / 2;
    uint8_t head[2];
    if (!readAt(header_.framesOffset + middle * header_.frameStride, head, sizeof(head))) {
      return false;
    }
    if (head[1] < lap) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  if (low >= header_.frameCount) return false;
  *frame = low;
  return true;
}

int RaceFile::loadDriverLaps(int driverIndex, LapTime* out, int maxLaps) {
  constexpr uint32_t CHUNK_RECORDS = 32;
  uint8_t chunk[CHUNK_RECORDS * RACE_LAP_RECORD_SIZE];
  int count = 0;
  for (uint32_t record = 0; record < header_.lapRecords && count < maxLaps;
       record += CHUNK_RECORDS) {
    uint32_t batch = header_.lapRecords - record;
    if (batch > CHUNK_RECORDS) batch = CHUNK_RECORDS;
    if (!readAt(header_.lapsOffset + record * RACE_LAP_RECORD_SIZE, chunk,
                batch * RACE_LAP_RECORD_SIZE)) {
      return count;
    }
    for (uint32_t i = 0; i < batch && count < maxLaps; i++) {
      const uint8_t* p = chunk + i * RACE_LAP_RECORD_SIZE;
      if (p[0] != driverIndex) continue;
      out[count].lap = p[1];
      out[count].flags = p[2];
      out[count].endMs = u32(p + 4);
      out[count].durationMs = u32(p + 8);
      count++;
    }
  }
  return count;
}
