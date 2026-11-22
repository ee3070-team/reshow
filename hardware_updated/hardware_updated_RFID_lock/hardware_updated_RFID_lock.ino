#include <stdint.h>
#include <EEPROM.h>
#include <SPI.h>
#include <Wire.h>
#include <MFRC522.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <GxEPD2_BW.h>
#include <stdio.h>
#include <string.h>
#include "LM35IC.h"
#include "rfid_cards.h"

using LM35::LM35IC;

// RC522 pin configuration for Arduino Mega 2560.
// You may change SS and RST to match your wiring.
// Hardware SPI pins are fixed on the Mega: MOSI=51, MISO=50, SCK=52.
const uint8_t rfidSsPin = 53;   // RC522 SDA/SS
const uint8_t rfidRstPin = 49;  // RC522 RST
const uint8_t epaperCsPin = 43;
const uint8_t epaperDcPin = 41;
const uint8_t epaperRstPin = 39;
const uint8_t epaperBusyPin = 37;
GxEPD2_BW<GxEPD2_154_D67, 40> epaper(
  GxEPD2_154_D67(epaperCsPin, epaperDcPin, epaperRstPin, epaperBusyPin));
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledReady = false;
const uint8_t doorRelayPin = 47; // Relay module IN
const bool doorRelayActiveLow = true; // Set false for a HIGH-trigger relay module
const unsigned long doorUnlockDuration = 3000;
bool doorUnlocked = false;
unsigned long doorUnlockedAt = 0;

// Temperature Sensor Pins and Variables
const int tempPin = A1;
LM35IC sensor = LM35IC(tempPin);
float currentTemp;

// Light Sensor Pins and Variables
const int lightPin = A0;
float currentLight;
float normalizedLight;
//Ultrasonic Sensor Pins and Variables
enum LightingMode {
  Auto,
  ForceON,
  ForceOFF
};

LightingMode lightingMode = Auto;
bool lightsOn = false;

const int trigPin = 3;
const int echoPin = 2;
const int redPin1 = 40;
const int greenPin1 = 42;
const int bluePin1 = 38;
const int redPin2 = 36;
const int greenPin2 = 34;
const int bluePin2 = 32;
unsigned long eventCount = 0;
const unsigned long epaperCardFeedbackDuration = 1000;
unsigned long epaperCardFeedbackStartedAt = 0;
bool epaperCardFeedbackActive = false;

//Clear Zone Delay 
const unsigned long clearZoneDelay = 1000; // Delay to turn off the lights when no occupancy is detected
unsigned long clearStartTime = 0;
bool clearTimerStarted = false;

//Interrupt Variables
volatile int d_flag = 1;
volatile unsigned long t1;
volatile unsigned long t2;
// Timer Variables
bool isOccupied = false;    //To check if the display is currently occupied
bool wasOccupied = false;   //To check if there was occupancy beforehand
unsigned long startTime;
unsigned long endTime;

// Environmental Readings Variables (Limit values can be adjusted depending on the artifact being displayed)
const int maxTempLimit = 24;      // Optimal temperatures can be set
const int minTempLimit = 18;
const int tempWarningFlag = 22;   // When temps hit 22C
const int maxLightLimit = 600;
const int minLightLimit = 300;
const unsigned long dataCollectTime = 30000; // Collect data every 5 mins (300000) (Can be reduced for testing)
const unsigned long dailyTime = 60000;      // 24hrs in ms (86400000) (can be reduced for testing)
float minChangeTemp = 999;
float maxChangeTemp = -999;
const int maxDailySwing = 2;                        // Max daily temperature swing of 2C
unsigned long previousMinuteTime = 0;
unsigned long previousDailyTime = 0;
unsigned long currentTime;

// Delay values if needed
const int delaySonic = 100;
const int delayTemp = 1000;
const int delayLight = 1000;

// Data Sending to ESP32
unsigned long lastSendTime = 0;
const unsigned long sendInterval = 10000;

// The card list itself lives in rfid_cards.h so every card is listed in one place.
const char rfidExhibitId[] = "helmet-001";
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



// Warning messages
char tempWarningText[] = "Temperature levels exceeds Safety Limits!";
char lightWarningText[] = "Illumination levels exceeds Safety Limits!";

void setupOled() {
  Wire.begin();
#if defined(WIRE_HAS_TIMEOUT)
  Wire.setWireTimeout(25000, true);
#endif
  const uint8_t addresses[] = {0x3C, 0x3D};
  for (uint8_t i = 0; i < sizeof(addresses); i++) {
    Wire.beginTransmission(addresses[i]);
    if (Wire.endTransmission() == 0) {
      if (!oled.begin(SSD1306_SWITCHCAPVCC, addresses[i], true, false)) {
        Serial.println("OLED initialization failed.");
        return;
      }
      oled.setRotation(3);
      oledReady = true;
      Serial.print("OLED found at 0x");
      Serial.println(addresses[i], HEX);
      return;
    }
  }
  Serial.println("OLED not found at 0x3C or 0x3D; check wiring and power.");
}

void updateOled(bool tempBreach, bool lightBreach) {
  if (!oledReady) return;
  oled.clearDisplay();
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(0, 0);
  oled.println(F("ReShow"));
  oled.println();
  oled.print(F("T: "));
  oled.print(currentTemp, 1);
  oled.println(F(" C"));
  oled.print(F("L: "));
  oled.print(normalizedLight, 0);
  oled.println(F(" / 100"));
  oled.println();
  oled.println(tempBreach ? F("T WARNING") : F("T: OK"));
  oled.println(lightBreach ? F("L WARNING") : F("L: OK"));
  oled.display();
}

void environmental_monitoring() {
  temp_reads();
  light_reads();
  bool tempBreach = (currentTemp >= maxTempLimit || currentTemp < minTempLimit);
  bool lightBreach = (currentLight > maxLightLimit || currentLight < minLightLimit);
  if (tempBreach) {
    Serial.println(tempWarningText);
    Serial.print("Temperature reached ");
    Serial.print(currentTemp);
    Serial.print("º C. Configured maximum was ");
    Serial.println(maxTempLimit);
  }
  else {
    Serial.println("Safe Temperature levels!");
  }
  if (lightBreach) {
    Serial.println(lightWarningText);
  }
  else {
    Serial.println("Safe light levels!");
  }
  updateOled(tempBreach, lightBreach);
  tempFluctuations();
}

void temp_reads() {
  currentTemp = sensor.readTemp();
  Serial.print("Temp: ");
  Serial.print(currentTemp);
  Serial.println("º C");
}

void light_reads() {
  currentLight = analogRead(lightPin);
  normalizedLight = (currentLight / 1023.0) * 100.0;
  Serial.print("Light: ");
  Serial.println(normalizedLight);
}

void tempFluctuations() {
  if (currentTemp < minChangeTemp) {
    minChangeTemp = currentTemp;
  }
  if (currentTemp > maxChangeTemp) {
    maxChangeTemp = currentTemp;
  }
}

void updateLighting() {
  switch (lightingMode) {
    case Auto:
      lightsOn = isOccupied;
      break;
    case ForceON:
      lightsOn = true;
      break;
    case ForceOFF:
      lightsOn = false;
      break;
  }

  if (epaperCardFeedbackActive &&
      millis() - epaperCardFeedbackStartedAt >= epaperCardFeedbackDuration) {
    epaperCardFeedbackActive = false;
  }
  const bool showGreen = epaperCardFeedbackActive;
  digitalWrite(redPin1, lightsOn && !showGreen ? HIGH : LOW);
  digitalWrite(greenPin1, lightsOn || showGreen ? HIGH : LOW);
  digitalWrite(bluePin1, lightsOn && !showGreen ? HIGH : LOW);
  digitalWrite(redPin2, lightsOn && !showGreen ? HIGH : LOW);
  digitalWrite(greenPin2, lightsOn || showGreen ? HIGH : LOW);
  digitalWrite(bluePin2, lightsOn && !showGreen ? HIGH : LOW);
}

void checkSerialCommands() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    if (command == 'A') {
      lightingMode = Auto;
      Serial.println("Lighting mode: AUTO");
    }
    else if (command == 'O') {
      lightingMode = ForceON;
      Serial.println("Lighting mode: FORCED ON");
    }
    else if (command == 'F') {
      lightingMode = ForceOFF;
      Serial.println("Lighting mode: FORCED OFF");
    }
    else if (command == 'L') {
      learnLastScannedRfidUid("door");
    }
    else if (command == 'M') {
      learnLastScannedRfidUid("edit");
    }
    else if (command == 'D') {
      dumpRfidUids();
    }
    else if (command == 'X') {
      clearLearnedRfidUids();
    }
  }
}

void sendRfidEventToESP32(const char *uid, const char *role) {
  const bool allowed = role != NULL;
  Serial2.print("{\"type\":\"rfid_event\",\"uid\":\"");
  Serial2.print(uid);
  Serial2.print("\",\"result\":\"");
  Serial2.print(allowed ? "accepted" : "denied");
  Serial2.print("\"");
  if (allowed) {
    Serial2.print(",\"exhibit_id\":\"");
    Serial2.print(rfidExhibitId);
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
      return learnedRfidCards[i].role;
    }
  }
  return NULL;
}

void checkEpaperResponse() {
  static char line[64];
  static uint8_t length = 0;
  static bool overflow = false;
  static char description[181];
  static uint16_t transferId = 0;
  static uint8_t expectedLength = 0;
  static uint8_t receivedLength = 0;
  static uint8_t nextChunk = 0;
  static bool receiving = false;
  while (Serial2.available()) {
    char c = Serial2.read();
    if (c == '\n') {
      line[length] = '\0';
      if (!overflow && strncmp(line, "EPD:", 4) == 0) {
        const char *phase = line + 4;
        if (strcmp(phase, "idle") == 0 || strcmp(phase, "pending_edit") == 0 ||
            strcmp(phase, "ready_to_confirm") == 0) {
          Serial.print("Server RFID status: ");
          Serial.println(phase);
          epaper.init(115200);
          epaper.setFullWindow();
          epaper.firstPage();
          do {
            epaper.fillScreen(GxEPD_WHITE);
            epaper.setTextColor(GxEPD_BLACK);
            epaper.setTextSize(2);
            epaper.setCursor(8, 40);
            epaper.println("Server status");
            epaper.setTextSize(1);
            epaper.setCursor(8, 85);
            epaper.println(phase);
          } while (epaper.nextPage());
          epaper.hibernate();
        } else {
          char *idEnd;
          unsigned long id = strtoul(line + 4, &idEnd, 10);
          if (idEnd == line + 4 || *idEnd != ':' || id == 0 || id > 65535) {
            Serial.println("Invalid e-paper message from ESP32.");
          } else {
            char *command = idEnd + 1;
            char *separator = strchr(command, ':');
            if (separator) *separator++ = '\0';
            bool accepted = false;
            if (strcmp(command, "B") == 0 && separator) {
              char *end;
              unsigned long size = strtoul(separator, &end, 10);
              receiving = *separator && !*end && size >= 1 && size <= 180;
              if (receiving) {
                transferId = (uint16_t)id;
                expectedLength = (uint8_t)size;
                receivedLength = 0;
                nextChunk = 0;
                accepted = true;
              }
            } else if (receiving && id == transferId && strcmp(command, "E") == 0 && !separator) {
              if (receivedLength == expectedLength) {
                description[receivedLength] = '\0';
                epaper.init(115200);
                epaper.setFullWindow();
                epaper.firstPage();
                do {
                  epaper.fillScreen(GxEPD_WHITE);
                  epaper.setTextColor(GxEPD_BLACK);
                  epaper.setTextSize(1);
                  epaper.setTextWrap(true);
                  epaper.setCursor(8, 12);
                  epaper.print(description);
                } while (epaper.nextPage());
                epaper.hibernate();
                receiving = false;
                accepted = true;
                Serial.println("E-paper description updated.");
              }
            } else if (receiving && id == transferId && separator) {
              char *end;
              unsigned long chunk = strtoul(command, &end, 10);
              size_t chunkLength = strlen(separator);
              if (*command && !*end && chunk == nextChunk && chunkLength > 0 &&
                  chunkLength <= 24 && receivedLength + chunkLength <= expectedLength) {
                memcpy(description + receivedLength, separator, chunkLength);
                receivedLength += chunkLength;
                nextChunk++;
                accepted = true;
              }
            }
            Serial2.print(accepted ? "EPD_ACK:" : "EPD_ERR:");
            Serial2.print(id);
            Serial2.print(':');
            Serial2.println(command);
          }
        }
      } else if (overflow) {
        Serial.println("Oversized message from ESP32.");
      }
      length = 0;
      overflow = false;
    } else if (c != '\r' && !overflow) {
      if (length < sizeof(line) - 1) line[length++] = c;
      else overflow = true;
    }
  }
}

void saveLearnedRfidUids() {
  EEPROM.write(eepromMagicAddress, eepromMagic);
  EEPROM.write(eepromCountAddress, learnedRfidCount);
  for (uint8_t i = 0; i < learnedRfidCount; i++) {
    const int base = eepromCardsAddress + i * eepromCardStride;
    for (uint8_t j = 0; j < rfidUidTextLength; j++) {
      EEPROM.write(base + j, learnedRfidCards[i].uid[j]);
    }
    for (uint8_t j = 0; j < rfidRoleTextLength; j++) {
      EEPROM.write(base + rfidUidTextLength + j, learnedRfidCards[i].role[j]);
    }
  }
}

void loadLearnedRfidUids() {
  learnedRfidCount = 0;
  if (EEPROM.read(eepromMagicAddress) != eepromMagic) {
    return;
  }
  learnedRfidCount = EEPROM.read(eepromCountAddress);
  if (learnedRfidCount > maxLearnedRfidUids) {
    learnedRfidCount = maxLearnedRfidUids;
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
      updateLighting();
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

void proximityUltrasonicSensor() {  //Main detection code for Auto
  if (d_flag==3) { // Echo Pin has been completed
    unsigned long distance = t2-t1; 
    if (distance < 900) { // ~ <15cm
      isOccupied = true;
      clearTimerStarted = false;
  }
    else {
      if (!clearTimerStarted) {
        clearStartTime = millis();
        clearTimerStarted = true;
      }
      if (millis() - clearStartTime >= clearZoneDelay) {
        isOccupied = false;
      }
    }
    if (isOccupied && !wasOccupied) {     // Turn on LEDs and start the Timer
      startTime = millis();
      eventCount++;
    }
    else if (!isOccupied && wasOccupied) { // Turn off LEDs and stop the Timer
      endTime = millis();
      unsigned long totalTime = endTime - startTime;
      Serial.print("Time of engagement: ");
      Serial.println(totalTime);
    }
  wasOccupied = isOccupied; 
  delay(delaySonic);
  d_flag = 1;
  digitalWrite(trigPin,LOW);
  delayMicroseconds(10);
  digitalWrite(trigPin,HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin,LOW);
  }
}

void setup() {
  // put your setup code here, to run once:
  Serial.begin(115200);
  // Communicate with ESP32
  Serial2.begin(9600);
  pinMode(doorRelayPin, OUTPUT);
  digitalWrite(doorRelayPin, doorRelayActiveLow ? HIGH : LOW);
  setDoorUnlocked(false);
  pinMode(rfidSsPin, OUTPUT);
  digitalWrite(rfidSsPin, HIGH);
  pinMode(epaperCsPin, OUTPUT);
  digitalWrite(epaperCsPin, HIGH);
  SPI.begin();
  Serial.println("Updating e-paper test screen...");
  epaper.init(115200);
  epaper.setRotation(0);
  epaper.setFullWindow();
  epaper.firstPage();
  do {
    epaper.fillScreen(GxEPD_WHITE);
    epaper.setTextColor(GxEPD_BLACK);
    epaper.setTextSize(2);
    epaper.setCursor(16, 80);
    epaper.println("ReShow");
    epaper.setCursor(16, 110);
    epaper.println("E-paper test");
  } while (epaper.nextPage());
  epaper.hibernate();
  Serial.println("E-paper test update finished; check the display.");
  Serial2.println("EPD_READY");
  rfidReader.PCD_Init();
  loadLearnedRfidUids();
  Serial.println("RC522 ready; scan a card to send an RFID event.");
  Serial.print("Allowlisted cards: ");
  Serial.println(rfidCardCount + learnedRfidCount);
  Serial.println("Serial commands: A/O/F lighting, L learn door card, M learn edit card, D dump cards, X clear learned.");
  // Temperature Sensor
  pinMode(tempPin, INPUT);
  // Light Sensor 
  pinMode(lightPin, INPUT);
  //Ultra Sensor 
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  // RGB LEDs
  pinMode(redPin1, OUTPUT);
  pinMode(greenPin1, OUTPUT);
  pinMode(bluePin1, OUTPUT);
  pinMode(redPin2, OUTPUT);
  pinMode(greenPin2, OUTPUT);
  pinMode(bluePin2, OUTPUT);
  attachInterrupt(digitalPinToInterrupt(echoPin), echoInterrupt, CHANGE);
  digitalWrite(trigPin,LOW);
  delayMicroseconds(10);
  digitalWrite(trigPin,HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin,LOW);
  currentTime = millis();
  previousMinuteTime = currentTime;
  previousDailyTime = currentTime;
  setupOled();
  environmental_monitoring();
}

 // To send the data to ESP32
void sendDataToESP32() {

  Serial2.print("{");

  Serial2.print("\"temperature\":");
  Serial2.print(currentTemp, 2);

  Serial2.print(",\"light\":");
  Serial2.print(normalizedLight, 0);

  Serial2.print(",\"occupied\":");
  Serial2.print(isOccupied ? "true" : "false");

  Serial2.print(",\"eventCount\":");
  Serial2.print(eventCount);

  Serial2.print(",\"lighting\":\"");

  switch (lightingMode) {

    case Auto:
      Serial2.print("AUTO");
      break;

    case ForceON:
      Serial2.print("FORCE_ON");
      break;

    case ForceOFF:
      Serial2.print("FORCE_OFF");
      break;
  }

  Serial2.println("\"}");
}

void loop() {
  checkSerialCommands();
  checkRfidReader();
  updateDoorLock();
  proximityUltrasonicSensor();
  updateLighting();
  // A full e-paper refresh can block the loop longer than the LED feedback.
  if (!epaperCardFeedbackActive) checkEpaperResponse();
  currentTime = millis();
  if (currentTime - previousMinuteTime > dataCollectTime) {
    environmental_monitoring();
    previousMinuteTime = currentTime;
  }
  if (currentTime - previousDailyTime >= dailyTime) {
    float dailySwing = maxChangeTemp - minChangeTemp;
    if (dailySwing > maxDailySwing) {
      Serial.println("Daily temperature swing exceeds limits!");
    }
    minChangeTemp = 999;
    maxChangeTemp = -999;
    previousDailyTime = currentTime;
  }
  if (currentTime - lastSendTime >= sendInterval) {
    sendDataToESP32();
    lastSendTime = currentTime;
  }
}

// Interrupt function for ultrasonic sensor
void echoInterrupt() { 
  if (d_flag== 1 && digitalRead(echoPin)==HIGH) {
    t1 = micros();
    d_flag = 2;
  } 
  if (d_flag == 2 && digitalRead(echoPin) == LOW) {
    t2 = micros();
    d_flag =3;
  }
}