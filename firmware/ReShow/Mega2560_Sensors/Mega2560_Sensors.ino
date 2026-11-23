/*
  ReShow: sensors -> Mega 2560 Serial2 -> ESP32-S3 -> AWS HTTPS.
  Upload this sketch to an Arduino Mega or Mega 2560 (ATmega2560).
  No extra libraries. See ../SETUP.txt for wiring and calibration.

  IMPORTANT: Mega TX2 pin 16 is 5 V. Use a 5 V -> 3.3 V level shifter
  or resistor divider before connecting it to ESP32-S3 GPIO18 (RX).
  Connect the boards' grounds. ESP TX is not needed for this workflow.

  UART: 9600 8N1, one checksummed ASCII line every 10 seconds:
  V1,seq,uptime_ms,temp_centi_c,light_adc,distance_mm,occupied,events,mode,output*CRC16
  temp=-32768 means invalid; distance=-1 / occupied=-1 means unknown.
  mode: 0=Auto, 1=On, 2=Off. CRC-16/CCITT-FALSE covers bytes before '*'.
  This protocol requires the matching ESP32S3_AWS sketch, not the old echo sketch.
*/
#include <Arduino.h>
#include <stdint.h>
#include <stdio.h>

const uint8_t TEMP_PIN = A1;
const uint8_t LIGHT_PIN = A0;
const uint8_t TRIG_PIN = 3;
const uint8_t ECHO_PIN = 2;
const uint8_t LED_PINS[] = {13, 12, 11, 48, 46, 44};
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
    occupancyKnown = false;
    clearTimerRunning = false;
    return;
  }
  distanceMm = (int16_t)((echoUs * 10UL + 29UL) / 58UL);
  occupancyKnown = true;
  if (distanceMm < OCCUPIED_DISTANCE_MM) {
    clearTimerRunning = false;
    if (!occupied) ++eventCount;  // Zone-entry events, not unique visitors.
    occupied = true;
  } else if (occupied) {
    if (!clearTimerRunning) {
      clearTimerRunning = true;
      clearStarted = now;
    }
    if ((uint32_t)(now - clearStarted) >= CLEAR_ZONE_MS) {
      occupied = false;
      clearTimerRunning = false;
    }
  }
}

void updateLighting() {
  // Explicit fault policy: Auto turns outputs off while proximity is unknown.
  lightsOn = lightingMode == 1 || (lightingMode == 0 && occupancyKnown && occupied);
  uint8_t level = (lightsOn == LED_ACTIVE_HIGH) ? HIGH : LOW;
  for (uint8_t i = 0; i < sizeof(LED_PINS); ++i) digitalWrite(LED_PINS[i], level);
}

void checkLocalCommands() {
  while (Serial.available()) {
    char command = (char)Serial.read();
    if (command == 'A' || command == 'a') lightingMode = 0;
    else if (command == 'O' || command == 'o') lightingMode = 1;
    else if (command == 'F' || command == 'f') lightingMode = 2;
    else continue;
    Serial.print(F("Local lighting mode (0=Auto,1=On,2=Off): "));
    Serial.println(lightingMode);
  }
}

void sendSample() {
  char body[128];
  char checksum[5];
  int length = snprintf(body, sizeof(body), "V1,%lu,%lu,%d,%u,%d,%d,%lu,%u,%u",
    (unsigned long)++sequenceNumber, (unsigned long)millis(),
    (int)temperatureCenti, (unsigned int)lightRaw, (int)distanceMm,
    occupancyKnown ? (occupied ? 1 : 0) : -1, (unsigned long)eventCount,
    (unsigned int)lightingMode, lightsOn ? 1U : 0U);
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

  if (temperatureCenti == INVALID_TEMP) Serial.println(F("LM35 reading invalid; check wiring/reference."));
  else if (temperatureCenti < 1800 || temperatureCenti >= 2400)
    Serial.println(F("Local warning: temperature outside original 18 to <24 C limits."));
  if (lightRaw < 300 || lightRaw > 600)
    Serial.println(F("Local warning: light outside original ADC 300..600 limits."));
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(9600);  // Mega TX2=16; RX2=17 unused.
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
  Serial.println(F("ReShow Mega ready. USB commands: A=Auto, O=On, F=Off."));
}

void loop() {
  checkLocalCommands();
  uint32_t now = millis();
  if ((uint32_t)(now - lastSonar) >= SONAR_INTERVAL_MS) {
    lastSonar = now;
    measureProximity();
  }
  updateLighting();
  now = millis();
  if ((uint32_t)(now - lastEnvironment) >= ENVIRONMENT_INTERVAL_MS) {
    lastEnvironment = now;
    readEnvironment();
  }
  if ((uint32_t)(now - lastSend) >= SEND_INTERVAL_MS) {
    lastSend = now;
    readEnvironment();
    sendSample();
  }
  if ((uint32_t)(now - dayStarted) >= DAY_MS) {
    if (dayMinTemp <= dayMaxTemp && dayMaxTemp - dayMinTemp > 200)
      Serial.println(F("Local warning: temperature swing exceeded 2 C over 24 hours."));
    dayMinTemp = 32767;
    dayMaxTemp = -32767;
    dayStarted = now;
  }
}
