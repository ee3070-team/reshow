/*
  ReShow: sensors -> Mega 2560 Serial2 -> ESP32-S3 -> AWS HTTPS.
  Upload this sketch to an Arduino Mega or Mega 2560 (ATmega2560).
  SSD1306 OLED warnings: Adafruit SSD1306 + Adafruit GFX libraries.
  See ../README.md for wiring and bench tests.

  IMPORTANT: Mega TX2 pin 16 is 5 V. Use a 5 V -> 3.3 V level shifter
  or resistor divider before connecting it to ESP32-S3 GPIO18 (RX).
  Connect the boards' grounds. ESP GPIO17/TX connects to Mega D17/RX2 for Mega e-paper updates.

  UART: 9600 8N1, one checksummed ASCII line every 10 seconds:
  V2,seq,uptime_ms,temp_centi_c,light_adc,distance_mm,occupied,events,mode,output,exhibit,generation*CRC16
  temp=-32768 means invalid; distance=-1 means no valid echo, reported as occupied=0.
  mode: 0=Auto, 1=On, 2=Off. CRC-16/CCITT-FALSE covers bytes before '*'.
  This protocol requires the matching ESP32S3_Final sketch, not the old echo sketch.
*/
#include <Arduino.h>
#include <stdint.h>
#include <stdio.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include "WarningState.h"
#include "AppliedProfile.h"
#include "RfidAccess.h"
#include "MegaPanel.h"

const uint8_t OLED_ADDRESS = 0x3C;  // Set 0x3D if that is your module's address.
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledReady = false;
WarningState warningState;
uint16_t warningMask = 0;
uint32_t lastOled = 0;

const uint8_t TEMP_PIN = A1;
const uint8_t LIGHT_PIN = A0;
const uint8_t TRIG_PIN = 3;
const uint8_t ECHO_PIN = 2;
const uint8_t LED_PINS[] = {40, 42, 38, 36, 34, 32};
const bool LED_ACTIVE_HIGH = true;  // Set false for common-anode RGB LEDs.

// DEFAULT ADC reference is the Mega's AVcc. Measure it and adjust this value.
const uint16_t ADC_REFERENCE_MV = 5000;
const uint32_t SEND_INTERVAL_MS = 10000UL;
const uint32_t ENVIRONMENT_INTERVAL_MS = 1000UL;
const uint32_t SONAR_INTERVAL_MS = 100UL;
const uint32_t SONAR_TIMEOUT_US = 25000UL;
const uint32_t CLEAR_ZONE_MS = 1000UL;
const int16_t OCCUPIED_DISTANCE_MM = 150;
const int16_t INVALID_TEMP = -32768;
const uint32_t DAY_MS = 86400000UL;

uint8_t lightingMode = 0;
bool lightsOn = false;
bool occupied = false;
uint32_t zoneEnteredAt = 0;
bool occupancyKnown = false;
bool clearTimerRunning = false;
uint32_t clearStarted = 0;
uint32_t eventCount = 0;
uint32_t sequenceNumber = 0;
int16_t temperatureCenti = INVALID_TEMP;
uint16_t lightRaw = 0;
int16_t distanceMm = -1;
int16_t dayMinTemp = 32767;
int16_t dayMaxTemp = -32767;
uint32_t lastEnvironment = 0;
uint32_t lastSonar = 0;
uint32_t lastSend = 0;
uint32_t dayStarted = 0;

uint16_t crc16(const char *text) {
  uint16_t crc = 0xFFFF;
  while (*text) {
    crc ^= (uint16_t)(uint8_t)*text++ << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

uint16_t readAverage(uint8_t pin) {
  analogRead(pin);  // Discard first conversion after switching ADC channels.
  uint32_t sum = 0;
  for (uint8_t i = 0; i < 16; ++i) sum += analogRead(pin);
  return (uint16_t)((sum + 8) / 16);
}

void readEnvironment() {
  uint16_t adc = readAverage(TEMP_PIN);
  // LM35 = 10 mV/degree C. Integer result is hundredths of a degree.
  int32_t centi = ((uint32_t)adc * ADC_REFERENCE_MV * 10UL + 511UL) / 1023UL;
  temperatureCenti = (adc == 0 || adc == 1023 || centi > 12500)
                       ? INVALID_TEMP : (int16_t)centi;
  lightRaw = readAverage(LIGHT_PIN);
  if (temperatureCenti != INVALID_TEMP) {
    if (temperatureCenti < dayMinTemp) dayMinTemp = temperatureCenti;
    if (temperatureCenti > dayMaxTemp) dayMaxTemp = temperatureCenti;
  }
}

void measureProximity() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  // Bounded wait: a missing echo cannot stop all future measurements.
  unsigned long echoUs = pulseIn(ECHO_PIN, HIGH, SONAR_TIMEOUT_US);
  uint32_t now = millis();
  if (echoUs == 0 || echoUs < 116UL || echoUs > 23200UL) {
    distanceMm = -1;
    // Requested policy: no valid echo is treated as an empty zone.
    if (occupied) {
      Serial.print(F("Time of engagement (ms): "));
      Serial.println(now - zoneEnteredAt);
    }
    occupied = false;
    occupancyKnown = true;
    clearTimerRunning = false;
    return;
  }
  distanceMm = (int16_t)((echoUs * 10UL + 29UL) / 58UL);
  occupancyKnown = true;
  if (distanceMm < OCCUPIED_DISTANCE_MM) {
    clearTimerRunning = false;
    if (!occupied) {
      ++eventCount;  // Zone entries, not unique visitors.
      zoneEnteredAt = now;
    }
    occupied = true;
  } else if (occupied) {
    if (!clearTimerRunning) {
      clearTimerRunning = true;
      clearStarted = now;
    }
    if ((uint32_t)(now - clearStarted) >= CLEAR_ZONE_MS) {
      occupied = false;
      clearTimerRunning = false;
      Serial.print(F("Time of engagement (ms): "));
      Serial.println(now - zoneEnteredAt);
    }
  }
}

void updateLighting() {
  // Normal lighting follows Auto/On/Off; any OLED warning overrides it with red.
  lightsOn = lightingMode == 1 || (lightingMode == 0 && occupancyKnown && occupied);
  if (millis() - epaperCardFeedbackStartedAt >= epaperCardFeedbackDuration)
    epaperCardFeedbackActive = false;
  for (uint8_t i = 0; i < sizeof(LED_PINS); ++i) {
    bool channelOn = warningMask ? (i == 0 || i == 3) :
      epaperCardFeedbackActive ? (i == 1 || i == 4) : lightsOn;
    digitalWrite(LED_PINS[i], channelOn == LED_ACTIVE_HIGH ? HIGH : LOW);
  }
  lightsOn = lightsOn || epaperCardFeedbackActive || warningMask != 0;
}

void checkLocalCommands() {
  while (Serial.available()) {
    char command = (char)Serial.read();
    if (command == 'A' || command == 'a') lightingMode = 0;
    else if (command == 'O' || command == 'o') lightingMode = 1;
    else if (command == 'F' || command == 'f') lightingMode = 2;
    else {
      switch (command) {
        case 'L': learnLastScannedRfidUid("door"); break;
        case 'M': learnLastScannedRfidUid("edit"); break;
        case 'D': dumpRfidUids(); break;
        case 'X': clearLearnedRfidUids(); break;
        case 'P': Serial.println(drawMegaPanel("ReShow e-paper bench test") ? F("Panel OK") : F("Panel busy or unavailable")); break;
      }
      continue;
    }
    Serial.print(F("Local lighting mode (0=Auto,1=On,2=Off): "));
    Serial.println(lightingMode);
  }
}

void beginOled() {
  Wire.begin();  // Mega SDA=20, SCL=21.
  Wire.setWireTimeout(25000, true);  // A failed I2C device must not freeze sensing.
  for (uint8_t address = OLED_ADDRESS; address <= 0x3D && !oledReady; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0)
      oledReady = oled.begin(SSD1306_SWITCHCAPVCC, address, true, false);
  }
  if (!oledReady) Serial.println(F("OLED unavailable; warnings continue on USB Serial."));
}

void profileChanged() {
  warningState.apply(appliedProfile);
  warningMask = 0;
  dayMinTemp = 32767; dayMaxTemp = -32767; dayStarted = millis();
  Serial.print(F("Applied exhibit: ")); Serial.print(appliedProfile.exhibit);
  Serial.print(F(" tag: ")); Serial.println(appliedProfile.tag);
  Serial.print(F("OLED limits: temperature "));
  Serial.print(appliedProfile.temperatureMin / 100.0, 2);
  Serial.print(F(" to ")); Serial.print(appliedProfile.temperatureMax / 100.0, 2);
  Serial.print(F(" C; light ADC ")); Serial.print(appliedProfile.lightMin);
  Serial.print(F(" to ")); Serial.print(appliedProfile.lightMax);
  Serial.print(F("; warning delay ")); Serial.print(appliedProfile.holdMs / 1000UL);
  Serial.println(F(" s"));
}

void updateWarnings() {
  bool swing = dayMinTemp <= dayMaxTemp && dayMaxTemp - dayMinTemp > 200;
  uint16_t next = warningState.update(temperatureCenti, lightRaw, distanceMm >= 0, swing, millis());
  if (next != warningMask) {
    Serial.print(F("OLED warning mask: 0x"));
    Serial.println(next, HEX);
    warningMask = next;
  }
}

void drawOled() {
  if (!oledReady) return;
  oled.clearDisplay();
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextWrap(false);
  oled.setCursor(0, 0);
  oled.println(appliedProfile.tag);
  oled.print(F("Temp: "));
  if (temperatureCenti == INVALID_TEMP) oled.println(F("-- C"));
  else { oled.print(temperatureCenti / 100.0, 1); oled.println(F(" C")); }
  oled.print(F("Light: ")); oled.print(lightRaw); oled.println(F(" /1023"));
  oled.print(F("Zone: "));
  oled.println(!occupancyKnown ? F("UNKNOWN") : occupied ? F("OCCUPIED") : F("CLEAR"));
  if (!warningMask) {
    // All eight OLED rows fit at text size 1. Faults take priority below.
    oled.println(appliedProfile.generation ? F("OK / fetched limits") : F("OK / bench limits"));
    oled.print(F("T: ")); oled.print(appliedProfile.temperatureMin / 100.0, 2);
    oled.print('-'); oled.print(appliedProfile.temperatureMax / 100.0, 2); oled.println(F(" C"));
    oled.print(F("L: ")); oled.print(appliedProfile.lightMin);
    oled.print('-'); oled.print(appliedProfile.lightMax); oled.println(F(" ADC"));
    oled.print(F("Hold: ")); oled.print(appliedProfile.holdMs / 1000UL); oled.println(F(" sec"));
  } else {
    // Up to six simultaneous faults; rotate through four rows without blocking.
    uint8_t count = 0;
    for (uint8_t i = 0; i < 8; ++i) if (warningMask & (1U << i)) ++count;
    uint8_t offset = count > 4 ? (millis() / 2000UL) % count : 0;
    for (uint8_t row = 0; row < (count < 4 ? count : 4); ++row) {
      uint8_t target = (offset + row) % count, index = 0;
      for (uint8_t i = 0; i < 8; ++i) {
        if (!(warningMask & (1U << i))) continue;
        if (index++ != target) continue;
        switch (i) {
          case 0: oled.println(F("E01 TEMP LOW")); break;
          case 1: oled.println(F("E02 TEMP HIGH")); break;
          case 2: oled.println(F("W01 TEMP CAUTION")); break;
          case 3: oled.println(F("W02 LIGHT LOW")); break;
          case 4: oled.println(F("E03 LIGHT HIGH")); break;
          case 5: oled.println(F("W03 TEMP SWING >2C")); break;
          case 6: oled.println(F("E04 TEMP SENSOR")); break;
          case 7: oled.println(F("E05 SONAR NO ECHO")); break;
        }
      }
    }
  }
  oled.display();
  if (Wire.getWireTimeoutFlag()) {
    Wire.clearWireTimeoutFlag();
    oledReady = false;
    Serial.println(F("OLED I2C timeout; display disabled until reset. Sensing continues."));
  }
}

void sendSample() {
  char body[220];
  char checksum[5];
  int length = snprintf(body, sizeof(body), "V2,%lu,%lu,%d,%u,%d,%d,%lu,%u,%u,%s,%lu",
    (unsigned long)++sequenceNumber, (unsigned long)millis(),
    (int)temperatureCenti, (unsigned int)lightRaw, (int)distanceMm,
    occupancyKnown ? (occupied ? 1 : 0) : -1, (unsigned long)eventCount,
    (unsigned int)lightingMode, lightsOn ? 1U : 0U, appliedProfile.exhibit,
    (unsigned long)appliedProfile.generation);
  if (length <= 0 || (size_t)length >= sizeof(body)) {
    Serial.println(F("ERROR: serial frame too long; sample dropped."));
    return;
  }
  snprintf(checksum, sizeof(checksum), "%04X", (unsigned int)crc16(body));
  Serial2.print(body);
  Serial2.print('*');
  Serial2.println(checksum);
  Serial.print(F("To ESP32: "));
  Serial.print(body);
  Serial.print('*');
  Serial.println(checksum);

  // Sustained warnings and sensor faults are handled by updateWarnings(),
  // independently of this 10-second telemetry interval.
}

void setup() {
  Serial.begin(115200);
  digitalWrite(doorRelayPin, doorRelayActiveLow ? HIGH : LOW);
  pinMode(doorRelayPin, OUTPUT);
  Serial2.begin(9600);
  digitalWrite(epaperCsPin, HIGH);
  pinMode(epaperCsPin, OUTPUT);
  digitalWrite(rfidSsPin, HIGH);
  pinMode(rfidSsPin, OUTPUT);
  SPI.begin();
  rfidReader.PCD_Init();
  loadLearnedRfidUids();
  loadAppliedProfile();
  profileChanged();
  Serial2.println(F("EPD_READY"));
  analogReference(DEFAULT);
  pinMode(TEMP_PIN, INPUT);
  pinMode(LIGHT_PIN, INPUT);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);
  for (uint8_t i = 0; i < sizeof(LED_PINS); ++i) {
    digitalWrite(LED_PINS[i], LED_ACTIVE_HIGH ? LOW : HIGH);
    pinMode(LED_PINS[i], OUTPUT);
  }
  readEnvironment();
  dayStarted = millis();
  beginOled();
  updateWarnings();
  updateLighting();
  drawOled();
  Serial.println(F("ReShow Mega ready. A/O/F lights, L/M learn door/edit, D list, X clear learned, P test Mega panel."));
}

void loop() {
  updateDoorLock();
  checkLocalCommands();
  checkRfidReader();
  if (!doorUnlocked && !epaperCardFeedbackActive) checkEpaperResponse();
  uint32_t now = millis();
  if ((uint32_t)(now - lastSonar) >= SONAR_INTERVAL_MS) {
    lastSonar = now;
    measureProximity();
  }
  now = millis();
  if ((uint32_t)(now - lastEnvironment) >= ENVIRONMENT_INTERVAL_MS) {
    lastEnvironment = now;
    readEnvironment();
  }
  updateWarnings();
  updateLighting();
  if ((uint32_t)(now - lastOled) >= 1000UL) {
    lastOled = now;
    drawOled();
  }
  if ((uint32_t)(now - lastSend) >= SEND_INTERVAL_MS) {
    lastSend = now;
    readEnvironment();
    sendSample();
  }
  if ((uint32_t)(now - dayStarted) >= DAY_MS) {
    if (!appliedProfile.generation && dayMinTemp <= dayMaxTemp && dayMaxTemp - dayMinTemp > 200)
      Serial.println(F("Local warning: temperature swing exceeded 2 C over 24 hours."));
    dayMinTemp = 32767;
    dayMaxTemp = -32767;
    dayStarted = now;
  }
}
