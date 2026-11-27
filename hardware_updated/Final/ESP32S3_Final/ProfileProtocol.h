#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

// Kept identical in both sketch folders. Wire data is ASCII, never a C struct.
struct ExhibitProfile {
  uint32_t generation = 0, version = 0;
  char exhibit[65] = "helmet-001", tag[65] = "UNASSIGNED", command[65] = "";
  int16_t temperatureMin = 1800, temperatureMax = 2400;
  uint16_t lightMin = 300, lightMax = 600;
  uint32_t holdMs = 5000;
};
constexpr size_t PROFILE_WIRE_SIZE = 280;
inline uint16_t profileCrc(const char *text) {
  uint16_t crc = 0xFFFF;
  while (*text) {
    crc ^= (uint16_t)(uint8_t)*text++ << 8;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}
inline bool profileIdentifier(const char *s) {
  size_t n = strlen(s);
  if (!n || n > 64) return false;
  for (size_t i = 0; i < n; ++i) {
    char c = s[i];
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || (i && (c == '-' || c == '_')))) return false;
  }
  return true;
}
inline bool profileNumber(const char *s, uint32_t maximum, uint32_t &out) {
  if (!*s) return false;
  uint32_t value = 0;
  while (*s) {
    if (*s < '0' || *s > '9' || value > maximum / 10) return false;
    value = value * 10 + (*s++ - '0');
    if (value > maximum) return false;
  }
  out = value;
  return true;
}
inline bool parseProfile(char *wire, ExhibitProfile &out) {
  char *parts[11];
  parts[0] = wire;
  for (uint8_t i = 1; i < 11; ++i) {
    char *comma = strchr(parts[i - 1], ',');
    if (!comma) return false;
    *comma = 0; parts[i] = comma + 1;
  }
  if (strchr(parts[10], ',')) return false;
  uint32_t n[7];
  const uint8_t positions[] = {0, 1, 5, 6, 7, 8, 9};
  const uint32_t maxima[] = {999999999UL, 2147483647UL, 12500, 12500, 1023, 1023, 3600};
  for (uint8_t i = 0; i < 7; ++i)
    if (!profileNumber(parts[positions[i]], maxima[i], n[i])) return false;
  if (strcmp(parts[10], "P1") || !n[0] || !n[1] || !n[6] ||
      n[2] >= n[3] || n[4] > n[5]) return false;
  for (uint8_t i = 2; i <= 4; ++i) if (!profileIdentifier(parts[i])) return false;
  ExhibitProfile p;
  p.generation = n[0]; p.version = n[1];
  strcpy(p.exhibit, parts[2]); strcpy(p.tag, parts[3]); strcpy(p.command, parts[4]);
  p.temperatureMin = n[2]; p.temperatureMax = n[3];
  p.lightMin = n[4]; p.lightMax = n[5]; p.holdMs = n[6] * 1000UL;
  out = p;
  return true;
}
inline bool formatProfile(const ExhibitProfile &p, char *wire, size_t capacity) {
  int n = snprintf(wire, capacity, "%lu,%lu,%s,%s,%s,%u,%u,%u,%u,%lu,P1",
    (unsigned long)p.generation, (unsigned long)p.version, p.exhibit, p.tag, p.command,
    (unsigned)p.temperatureMin, (unsigned)p.temperatureMax, (unsigned)p.lightMin,
    (unsigned)p.lightMax, (unsigned long)(p.holdMs / 1000));
  return n > 0 && (size_t)n < capacity;
}
