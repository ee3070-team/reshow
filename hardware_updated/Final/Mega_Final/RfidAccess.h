#pragma once
#include <EEPROM.h>
#include <SPI.h>
#include <MFRC522.h>
#include "rfid_cards.h"

const uint8_t rfidSsPin = 53;   // RC522 SDA/SS
const uint8_t rfidRstPin = 49;  // RC522 RST
const uint8_t doorRelayPin = 47; // Relay module IN
const bool doorRelayActiveLow = true; // Set false for a HIGH-trigger relay module
const unsigned long doorUnlockDuration = 3000;
bool doorUnlocked = false;
unsigned long doorUnlockedAt = 0;

const unsigned long epaperCardFeedbackDuration = 1000;
unsigned long epaperCardFeedbackStartedAt = 0;
bool epaperCardFeedbackActive = false;

// Exhibit identity follows the committed monitoring profile.
const unsigned long rfidRepeatDelay = 2000;
unsigned long lastRfidScanAt = 0;
char lastRfidUid[21] = "";
char lastScannedRfidUid[21] = "";
MFRC522 rfidReader(rfidSsPin, rfidRstPin);

// Cards learned at runtime with the serial commands 'L' (door) and 'M' (edit)
// are saved in EEPROM so they survive a reset without another sketch upload.
const int eepromMagicAddress = 0;
const int eepromCountAddress = 1;
const int eepromCardsAddress = 2;
const uint8_t eepromMagic = 0x52;
const uint8_t maxLearnedRfidUids = 16;
const uint8_t rfidUidTextLength = 21;
const uint8_t rfidRoleTextLength = 5;
const uint8_t eepromCardStride = rfidUidTextLength + rfidRoleTextLength;

struct LearnedRfidCard {
  char uid[rfidUidTextLength];
  char role[rfidRoleTextLength];
};

uint8_t learnedRfidCount = 0;
LearnedRfidCard learnedRfidCards[maxLearnedRfidUids];



void sendRfidEventToESP32(const char *uid, const char *role) {
  const bool allowed = role != NULL;
  Serial2.print("{\"type\":\"rfid_event\",\"uid\":\"");
  Serial2.print(uid);
  Serial2.print("\",\"result\":\"");
  Serial2.print(allowed ? "accepted" : "denied");
  Serial2.print("\"");
  if (allowed) {
    Serial2.print(",\"exhibit_id\":\"");
    Serial2.print(appliedProfile.exhibit);
    Serial2.print("\",\"role\":\"");
    Serial2.print(role);
    Serial2.print("\"");
  }
  Serial2.println("}");

  Serial.print("RFID ");
  Serial.print(allowed ? "accepted (" : "denied: ");
  if (allowed) {
    Serial.print(role);
    Serial.print("): ");
  }
  Serial.println(uid);
  if (!allowed) {
    Serial.println("Send 'L' to learn it as a door card or 'M' as an edit card.");
  }
}

void setDoorUnlocked(bool unlocked) {
  const uint8_t activeLevel = doorRelayActiveLow ? LOW : HIGH;
  const uint8_t lockedLevel = doorRelayActiveLow ? HIGH : LOW;
  digitalWrite(doorRelayPin, unlocked ? activeLevel : lockedLevel);
  doorUnlocked = unlocked;
  if (unlocked) {
    doorUnlockedAt = millis();
    Serial.println("Door unlocked; it will relock automatically.");
  } else {
    Serial.println("Door locked.");
  }
}

void updateDoorLock() {
  if (doorUnlocked && millis() - doorUnlockedAt >= doorUnlockDuration) {
    setDoorUnlocked(false);
  }
}

const char *rfidRoleFor(const char *uid) {
  for (uint8_t i = 0; i < rfidCardCount; i++) {
    if (strcmp(uid, rfidCards[i].uid) == 0) {
      return rfidCards[i].role[0] == '\0' ? NULL : rfidCards[i].role;
    }
  }
  for (uint8_t i = 0; i < learnedRfidCount; i++) {
    if (strcmp(uid, learnedRfidCards[i].uid) == 0) {
      const char *role = learnedRfidCards[i].role;
      return (!strcmp(role, "door") || !strcmp(role, "edit")) ? role : NULL;
    }
  }
  return NULL;
}

void saveLearnedRfidUids() {
  for (uint8_t i = 0; i < learnedRfidCount; i++) {
    const int base = eepromCardsAddress + i * eepromCardStride;
    for (uint8_t j = 0; j < rfidUidTextLength; j++) {
      EEPROM.update(base + j, learnedRfidCards[i].uid[j]);
    }
    for (uint8_t j = 0; j < rfidRoleTextLength; j++) {
      EEPROM.update(base + rfidUidTextLength + j, learnedRfidCards[i].role[j]);
    }
  }
  EEPROM.update(eepromCountAddress, learnedRfidCount);
  EEPROM.update(eepromMagicAddress, eepromMagic);
}

void loadLearnedRfidUids() {
  learnedRfidCount = 0;
  if (EEPROM.read(eepromMagicAddress) != eepromMagic) {
    return;
  }
  learnedRfidCount = EEPROM.read(eepromCountAddress);
  if (learnedRfidCount > maxLearnedRfidUids) {
    learnedRfidCount = 0;
  }
  for (uint8_t i = 0; i < learnedRfidCount; i++) {
    const int base = eepromCardsAddress + i * eepromCardStride;
    for (uint8_t j = 0; j < rfidUidTextLength; j++) {
      learnedRfidCards[i].uid[j] = (char)EEPROM.read(base + j);
    }
    learnedRfidCards[i].uid[rfidUidTextLength - 1] = '\0';
    for (uint8_t j = 0; j < rfidRoleTextLength; j++) {
      learnedRfidCards[i].role[j] =
        (char)EEPROM.read(base + rfidUidTextLength + j);
    }
    learnedRfidCards[i].role[rfidRoleTextLength - 1] = '\0';
  }
}

void learnLastScannedRfidUid(const char *role) {
  if (lastScannedRfidUid[0] == '\0') {
    Serial.println("No card scanned yet; tap a card first.");
    return;
  }
  const char *existingRole = rfidRoleFor(lastScannedRfidUid);
  if (existingRole != NULL) {
    Serial.print("Already allowed as ");
    Serial.print(existingRole);
    Serial.print(": ");
    Serial.println(lastScannedRfidUid);
    return;
  }
  for (uint8_t i = 0; i < rfidCardCount; ++i) {
    if (!strcmp(lastScannedRfidUid, rfidCards[i].uid)) {
      Serial.println(F("This card has a fixed role in rfid_cards.h."));
      return;
    }
  }
  if (learnedRfidCount >= maxLearnedRfidUids) {
    Serial.println("Learned card list is full; send 'X' to clear it.");
    return;
  }
  strncpy(learnedRfidCards[learnedRfidCount].uid, lastScannedRfidUid,
    rfidUidTextLength - 1);
  learnedRfidCards[learnedRfidCount].uid[rfidUidTextLength - 1] = '\0';
  strncpy(learnedRfidCards[learnedRfidCount].role, role,
    rfidRoleTextLength - 1);
  learnedRfidCards[learnedRfidCount].role[rfidRoleTextLength - 1] = '\0';
  learnedRfidCount++;
  saveLearnedRfidUids();
  Serial.print("Learned as ");
  Serial.print(role);
  Serial.print(": ");
  Serial.println(lastScannedRfidUid);
}

void dumpRfidUids() {
  Serial.println("Allowlisted cards:");
  for (uint8_t i = 0; i < rfidCardCount; i++) {
    Serial.print("  [rfid_cards.h] ");
    Serial.print(rfidCards[i].uid);
    Serial.print(" -> ");
    Serial.println(rfidCards[i].role);
  }
  for (uint8_t i = 0; i < learnedRfidCount; i++) {
    Serial.print("  [learned] ");
    Serial.print(learnedRfidCards[i].uid);
    Serial.print(" -> ");
    Serial.println(learnedRfidCards[i].role);
  }
}

void clearLearnedRfidUids() {
  learnedRfidCount = 0;
  saveLearnedRfidUids();
  Serial.println("Learned cards cleared.");
}

void checkRfidReader() {
  if (!rfidReader.PICC_IsNewCardPresent() || !rfidReader.PICC_ReadCardSerial()) {
    return;
  }

  char uid[21] = "";
  byte uidLength = rfidReader.uid.size;
  if (uidLength > 10) {
    uidLength = 10;
  }
  for (byte i = 0; i < uidLength; i++) {
    char hexByte[3];
    snprintf(hexByte, sizeof(hexByte), "%02X", rfidReader.uid.uidByte[i]);
    strcat(uid, hexByte);
  }
  strncpy(lastScannedRfidUid, uid, sizeof(lastScannedRfidUid) - 1);
  lastScannedRfidUid[sizeof(lastScannedRfidUid) - 1] = '\0';

  const unsigned long now = millis();
  const bool repeatedRead = strcmp(uid, lastRfidUid) == 0
    && now - lastRfidScanAt < rfidRepeatDelay;
  if (!repeatedRead) {
    const char *role = rfidRoleFor(uid);
    if (role != NULL && strcmp(role, "epaper") == 0) {
      epaperCardFeedbackStartedAt = millis();
      epaperCardFeedbackActive = true;
    }
    if (role != NULL && strcmp(role, "wifi") == 0) {
      Serial.print("RFID wifi test card: ");
      Serial.println(uid);
      Serial2.println("{\"type\":\"wifi_test\",\"message\":\"Hello from ReShow\"}");
    } else {
      sendRfidEventToESP32(uid, role);
    }
    if (role != NULL && strcmp(role, "door") == 0 && !doorUnlocked) {
      setDoorUnlocked(true);
    }
    strncpy(lastRfidUid, uid, sizeof(lastRfidUid) - 1);
    lastRfidUid[sizeof(lastRfidUid) - 1] = '\0';
    lastRfidScanAt = now;
  }

  rfidReader.PICC_HaltA();
  rfidReader.PCD_StopCrypto1();
}
