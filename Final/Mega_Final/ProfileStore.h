#pragma once
#include <EEPROM.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "WarningState.h"

namespace ProfileStore {
// The RFID card store occupies bytes 0..417. Each profile slot has its own
// commit byte so an interrupted write cannot replace the previous profile.
constexpr int SLOT_BASE = 512;
constexpr int SLOT_SIZE = 256;
constexpr uint8_t COMMITTED = 0xA7;

struct Profile {
  uint32_t generation;
  uint32_t version;
  char exhibit[65];
  char command[65];
  MonitoringLimits limits;
  uint16_t checksum;
};
static_assert(sizeof(Profile) < SLOT_SIZE - 1, "Profile slot too small");

enum Result { EMPTY, VALID, CORRUPT };

uint16_t checksum(const Profile &profile) {
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&profile);
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < offsetof(Profile, checksum); ++i) {
    crc ^= (uint16_t)bytes[i] << 8;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

bool validIdentifier(const char *text) {
  for (uint8_t i = 0; i < 65; ++i) {
    char c = text[i];
    if (!c) return i != 0;
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
          (c >= '0' && c <= '9') || (i && (c == '-' || c == '_')))) return false;
  }
  return false;
}

bool valid(const Profile &profile) {
  WarningState check;
  return profile.generation > 0 && profile.generation <= 999999999UL &&
         profile.version > 0 && profile.version <= 2147483647UL &&
         validIdentifier(profile.exhibit) && validIdentifier(profile.command) &&
         check.applyProfile(profile.limits);
}

bool same(const Profile &a, const Profile &b) {
  return a.generation == b.generation && a.version == b.version &&
         !strcmp(a.exhibit, b.exhibit) && !strcmp(a.command, b.command) &&
         a.limits.temperatureMin == b.limits.temperatureMin &&
         a.limits.temperatureMax == b.limits.temperatureMax &&
         a.limits.lightMin == b.limits.lightMin && a.limits.lightMax == b.limits.lightMax &&
         a.limits.warningSeconds == b.limits.warningSeconds;
}

Result readSlot(uint8_t slot, Profile &profile) {
  int base = SLOT_BASE + slot * SLOT_SIZE;
  uint8_t marker = EEPROM.read(base);
  if (marker == 0 || marker == 0xFF) return EMPTY;
  if (marker != COMMITTED) return CORRUPT;
  uint8_t *bytes = reinterpret_cast<uint8_t *>(&profile);
  for (size_t i = 0; i < sizeof(profile); ++i) bytes[i] = EEPROM.read(base + 1 + i);
  return valid(profile) && checksum(profile) == profile.checksum ? VALID : CORRUPT;
}

Result load(Profile &profile, uint8_t &slot) {
  Profile first = {}, second = {};
  Result a = readSlot(0, first), b = readSlot(1, second);
  if (a == CORRUPT || b == CORRUPT) return CORRUPT;
  if (a == EMPTY && b == EMPTY) return EMPTY;
  if (a == VALID && b == VALID && first.generation == second.generation && !same(first, second))
    return CORRUPT;
  slot = (b == VALID && (a == EMPTY || second.generation > first.generation)) ? 1 : 0;
  profile = slot ? second : first;
  return VALID;
}

bool save(const Profile &input) {
  if (!valid(input)) return false;
  Profile current = {};
  uint8_t active = 0;
  Result state = load(current, active);
  if (state == CORRUPT) return false;
  if (state == VALID && input.generation <= current.generation)
    return input.generation == current.generation && same(input, current);
  Profile next = {};
  next.generation = input.generation;
  next.version = input.version;
  strcpy(next.exhibit, input.exhibit);
  strcpy(next.command, input.command);
  next.limits = input.limits;
  next.checksum = checksum(next);
  int base = SLOT_BASE + (state == VALID ? 1 - active : 0) * SLOT_SIZE;
  EEPROM.update(base, 0);
  if (EEPROM.read(base) != 0) return false;
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&next);
  for (size_t i = 0; i < sizeof(next); ++i) EEPROM.update(base + 1 + i, bytes[i]);
  for (size_t i = 0; i < sizeof(next); ++i)
    if (EEPROM.read(base + 1 + i) != bytes[i]) return false;
  EEPROM.update(base, COMMITTED);
  Profile written = {};
  return readSlot((base - SLOT_BASE) / SLOT_SIZE, written) == VALID && same(next, written);
}
}
