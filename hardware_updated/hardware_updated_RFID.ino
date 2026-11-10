#include <stdint.h>
#include <SPI.h>
#include <MFRC522.h>
#include <stdio.h>
#include <string.h>
#include "LM35IC.h"

using LM35::LM35IC;

// RC522 pin configuration for Arduino Mega 2560.
// You may change SS and RST to match your wiring.
// Hardware SPI pins are fixed on the Mega: MOSI=51, MISO=50, SCK=52.
const uint8_t rfidSsPin = 53;   // RC522 SDA/SS
const uint8_t rfidRstPin = 49;  // RC522 RST

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
const int redPin1 = 13;
const int greenPin1 = 12;
const int bluePin1 = 11;
const int redPin2 = 48;
const int greenPin2 = 46;
const int bluePin2 = 44;
unsigned long eventCount = 0;

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

const char allowedRfidUid[] = "04833912EE1B90";
const char rfidExhibitId[] = "helmet-001";
const unsigned long rfidRepeatDelay = 2000;
unsigned long lastRfidScanAt = 0;
char lastRfidUid[21] = "";
MFRC522 rfidReader(rfidSsPin, rfidRstPin);



// Warning messages
char tempWarningText[] = "Temperature levels exceeds Safety Limits!";
char lightWarningText[] = "Illumination levels exceeds Safety Limits!";



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

  digitalWrite(redPin1, lightsOn ? HIGH : LOW);
  digitalWrite(greenPin1, lightsOn ? HIGH : LOW);
  digitalWrite(bluePin1, lightsOn ? HIGH : LOW);
  digitalWrite(redPin2, lightsOn ? HIGH : LOW);
  digitalWrite(greenPin2, lightsOn ? HIGH : LOW);
  digitalWrite(bluePin2, lightsOn ? HIGH : LOW);
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
  }
}

void sendRfidEventToESP32(const char *uid, bool allowed) {
  Serial2.print("{\"type\":\"rfid_event\",\"uid\":\"");
  Serial2.print(uid);
  Serial2.print("\",\"result\":\"");
  Serial2.print(allowed ? "accepted" : "denied");
  Serial2.print("\"");
  if (allowed) {
    Serial2.print(",\"exhibit_id\":\"");
    Serial2.print(rfidExhibitId);
    Serial2.print("\"");
  }
  Serial2.println("}");

  Serial.print("RFID ");
  Serial.print(allowed ? "accepted: " : "denied: ");
  Serial.println(uid);
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

  const unsigned long now = millis();
  const bool repeatedRead = strcmp(uid, lastRfidUid) == 0
    && now - lastRfidScanAt < rfidRepeatDelay;
  if (!repeatedRead) {
    const bool allowed = strcmp(uid, allowedRfidUid) == 0;
    sendRfidEventToESP32(uid, allowed);
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
  pinMode(rfidSsPin, OUTPUT);
  SPI.begin();
  rfidReader.PCD_Init();
  Serial.println("RC522 ready; scan a card to send an RFID event.");
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
  proximityUltrasonicSensor();
  updateLighting();
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