#pragma once
#include <EEPROM.h>
#include "ProfileProtocol.h"

ExhibitProfile appliedProfile;
// RFID learning occupies bytes 0..417. Two independent committed profile slots.
constexpr int PROFILE_EEPROM_BASE = 512;
constexpr int PROFILE_SLOT_SIZE = PROFILE_WIRE_SIZE + 3;
int profileSlot = -1;
char appliedWire[PROFILE_WIRE_SIZE] = "";

bool readProfileSlot(int slot, char *wire, ExhibitProfile &profile) {
  int base = PROFILE_EEPROM_BASE + slot * PROFILE_SLOT_SIZE;
  if (EEPROM.read(base) != 0xA7) return false;
  for (size_t i = 0; i < PROFILE_WIRE_SIZE; ++i) wire[i] = EEPROM.read(base + 3 + i);
  if (wire[PROFILE_WIRE_SIZE - 1]) return false;
  uint16_t crc = EEPROM.read(base + 1) | ((uint16_t)EEPROM.read(base + 2) << 8);
  if (crc != profileCrc(wire)) return false;
  char copy[PROFILE_WIRE_SIZE]; strcpy(copy, wire);
  return parseProfile(copy, profile);
}
void loadAppliedProfile() {
  char wire[PROFILE_WIRE_SIZE]; ExhibitProfile p;
  for (int slot = 0; slot < 2; ++slot) {
    if (readProfileSlot(slot, wire, p) && p.generation > appliedProfile.generation) {
      appliedProfile = p; strcpy(appliedWire, wire); profileSlot = slot;
    }
  }
}
bool persistProfile(const char *wire, const ExhibitProfile &p) {
  // Duplicate commits are safe; changed payloads cannot reuse a generation.
  if (p.generation <= appliedProfile.generation)
    return p.generation == appliedProfile.generation && !strcmp(wire, appliedWire);
  int slot = profileSlot == 0 ? 1 : 0;
  int base = PROFILE_EEPROM_BASE + slot * PROFILE_SLOT_SIZE;
  EEPROM.update(base, 0);
  size_t length = strlen(wire);
  for (size_t i = 0; i < PROFILE_WIRE_SIZE; ++i)
    EEPROM.update(base + 3 + i, i <= length ? wire[i] : 0);
  uint16_t crc = profileCrc(wire);
  EEPROM.update(base + 1, crc & 255); EEPROM.update(base + 2, crc >> 8);
  EEPROM.update(base, 0xA7); // Commit marker is written last; previous slot survives interruption.
  char check[PROFILE_WIRE_SIZE]; ExhibitProfile saved;
  if (!readProfileSlot(slot, check, saved) || strcmp(check, wire)) return false;
  appliedProfile = saved; strcpy(appliedWire, wire); profileSlot = slot;
  return true;
}
void profileChanged();

// Chunk ACKs keep every UART message below the AVR's 64-byte RX ring.
bool handleProfileLine(char *line) {
  if (strncmp(line, "CFG:", 4)) return false;
  static char wire[PROFILE_WIRE_SIZE];
  static uint16_t transferId = 0, expected = 0, used = 0, checksum = 0, chunk = 0;
  char *part = strchr(line + 4, ':');
  if (!part) return true;
  *part++ = 0;
  uint32_t id;
  if (!profileNumber(line + 4, 65535, id) || !id) return true;
  char *payload = strchr(part, ':');
  if (payload) *payload++ = 0;
  bool ok = false;
  if (!strcmp(part, "B") && payload) {
    char *crc = strchr(payload, ','); uint32_t size, value;
    if (crc) {
      *crc++ = 0;
      if (profileNumber(payload, PROFILE_WIRE_SIZE - 1, size) && size &&
          profileNumber(crc, 65535, value)) {
        transferId = id; expected = size; checksum = value; used = chunk = 0; ok = true;
      }
    }
  } else if (transferId == id && !strcmp(part, "E") && payload) {
    uint32_t supplied;
    if (profileNumber(payload, 65535, supplied) && supplied == checksum && used == expected) {
      wire[used] = 0;
      char copy[PROFILE_WIRE_SIZE]; strcpy(copy, wire); ExhibitProfile p;
      if (profileCrc(wire) == checksum && parseProfile(copy, p)) {
        bool changed = p.generation != appliedProfile.generation;
        ok = persistProfile(wire, p);
        if (ok && changed) profileChanged();
      }
    }
    transferId = 0;
  } else if (transferId == id && payload) {
    uint32_t index; size_t length = strlen(payload);
    if (profileNumber(part, 20, index) && index == chunk && length && length <= 24 && used + length <= expected) {
      memcpy(wire + used, payload, length); used += length; ++chunk; ok = true;
    }
  }
  Serial2.print(ok ? F("CFG_ACK:") : F("CFG_ERR:"));
  Serial2.print(id); Serial2.print(':'); Serial2.print(part);
  if (!strcmp(part, "E") && payload) { Serial2.print(':'); Serial2.print(payload); }
  Serial2.println();
  return true;
}
