#include "manifest.h"

#include <stdlib.h>
#include <string.h>

namespace {
constexpr int FIELD_COUNT = 11;
}

int Manifest::nextByte() {
  if (bufferIndex_ >= bufferLength_) {
    bufferOffset_ += bufferLength_;
    const int32_t got = source_->read(buffer_, sizeof(buffer_));
    bufferLength_ = got > 0 ? got : 0;
    bufferIndex_ = 0;
    if (bufferLength_ == 0) return -1;
  }
  return buffer_[bufferIndex_++];
}

bool Manifest::readLine(char* dst, int dstSize) {
  int length = 0;
  int value = nextByte();
  if (value < 0) return false;
  while (value >= 0 && value != '\n') {
    if (value != '\r' && length < dstSize - 1) dst[length++] = char(value);
    value = nextByte();
  }
  dst[length] = 0;
  return true;
}

ManifestError Manifest::open(ByteSource* source) {
  source_ = source;
  bufferOffset_ = 0;
  bufferLength_ = 0;
  bufferIndex_ = 0;
  if (source == nullptr || !source->seek(0)) return ManifestError::Empty;
  char line[MANIFEST_MAX_LINE];
  if (!readLine(line, sizeof(line))) return ManifestError::Empty;
  if (strncmp(line, "F1RM\t", 5) != 0) return ManifestError::NotAManifest;
  if (atoi(line + 5) != 1) return ManifestError::WrongVersion;
  firstRowOffset_ = bufferOffset_ + bufferIndex_;
  return ManifestError::None;
}

void Manifest::rewind() {
  source_->seek(firstRowOffset_);
  bufferOffset_ = firstRowOffset_;
  bufferLength_ = 0;
  bufferIndex_ = 0;
}

bool parseManifestLine(ManifestRow* row) {
  char* fields[FIELD_COUNT];
  char* cursor = row->line;
  for (int i = 0; i < FIELD_COUNT; i++) {
    fields[i] = cursor;
    char* tab = strchr(cursor, '\t');
    if (tab == nullptr) {
      if (i != FIELD_COUNT - 1) return false;
    } else {
      *tab = 0;
      cursor = tab + 1;
    }
  }
  row->season = uint16_t(atoi(fields[0]));
  row->round = uint8_t(atoi(fields[1]));
  row->status = fields[2][0];
  row->fidelity = fields[3][0];
  row->file = fields[4];
  row->size = uint32_t(strtoul(fields[5], nullptr, 10));
  row->crc32 = uint32_t(strtoul(fields[6], nullptr, 16));
  row->name = fields[7];
  row->circuit = fields[8];
  row->date = fields[9];
  row->note = fields[10];
  return row->season != 0 && row->round != 0;
}

bool Manifest::nextRow(ManifestRow* row) {
  while (readLine(row->line, sizeof(row->line))) {
    if (parseManifestLine(row)) return true;  // Skip malformed lines.
  }
  return false;
}

int Manifest::collectSeasons(uint16_t* out, int maxSeasons) {
  static ManifestRow row;
  int count = 0;
  rewind();
  while (nextRow(&row)) {
    bool seen = false;
    for (int i = 0; i < count && !seen; i++) seen = out[i] == row.season;
    if (!seen && count < maxSeasons) out[count++] = row.season;
  }
  for (int i = 1; i < count; i++) {  // Insertion sort, newest first.
    const uint16_t value = out[i];
    int j = i - 1;
    while (j >= 0 && out[j] < value) {
      out[j + 1] = out[j];
      j--;
    }
    out[j + 1] = value;
  }
  return count;
}

int Manifest::collectRaces(uint16_t season, RaceListItem* out, int maxRaces) {
  static ManifestRow row;
  int count = 0;
  rewind();
  while (nextRow(&row) && count < maxRaces) {
    if (row.season != season) continue;
    RaceListItem& item = out[count++];
    item.round = row.round;
    item.status = row.status;
    item.fidelity = row.fidelity;
    strncpy(item.name, row.name, sizeof(item.name) - 1);
    item.name[sizeof(item.name) - 1] = 0;
  }
  return count;
}

bool Manifest::findRace(uint16_t season, uint8_t round, ManifestRow* row) {
  rewind();
  while (nextRow(row)) {
    if (row->season == season && row->round == round) return true;
  }
  return false;
}
