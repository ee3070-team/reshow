#include <stdint.h>
#include "LM35IC.h"

using LM35::LM35IC;

// Temperature Sensor Pins and Variables
const int tempPin = A1;
LM35IC sensor = LM35IC(tempPin);
float currentTemp;

// Light Sensor Pins and Variables
const int lightPin = A0;
float currentLight;

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
const int maxTempLimit = 24;      // Optimal temperature between 18C to 22C, never exceed 24 (Temps should not swing within a 24hr period), Add 2 warnings (1: Exceeds 22C, 2: Exceeds 24C);
const int minTempLimit = 18;
const int tempWarningFlag = 22;   // When temps hit 22C
const int lightLimit = 600;
const unsigned long dataCollectTime = 30000; // Collect data every 5 mins (300000) (Can be reduced for testing)
const unsigned long dailyTime = 60000;      // 24hrs in ms (86400000) (can be reduced for testing)
float minChangeTemp = 999;
float maxChangeTemp = -999;
const int maxDailySwing = 2;                        // Max temperature swing of 2C
unsigned long previousMinuteTime = 0;
unsigned long previousDailyTime = 0;
unsigned long currentTime;
// Delay values
const int delaySonic = 100;
const int delayTemp = 1000;
const int delayLight = 1000;

// Warning messages
char tempWarnText[] = "Temperature levels approaching warning limits. Please check the display";
char tempWarningText[] = "Temperature levels exceeds Safety Limits!";
char lightWarningText[] = "Illumination levels exceeds Safety Limits!";

void environmental_monitoring() {
  temp_reads();
  light_reads();
  bool tempBreach = (currentTemp >= maxTempLimit || currentTemp < minTempLimit);
  bool tempWarning = (currentTemp >= tempWarningFlag && !tempBreach);
  bool lightBreach = (currentLight > lightLimit);
  if (tempWarning) {
    Serial.println(tempWarnText);
  }
  else if (tempBreach) {
    Serial.println(tempWarningText);
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
  delay(delayTemp);
}

void light_reads() {
  currentLight = analogRead(lightPin);
  Serial.print("Light: ");
  Serial.println(currentLight);
  delay(delayLight);
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
  Serial.begin(9600);
  // Temperature Sensor pin modes and initialisation
  pinMode(tempPin, INPUT);
  // Light Sensor pin modes and initialisation
  pinMode(lightPin, INPUT);
  //Ultra Sensor pin modes and initialisation
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
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

void loop() {
  checkSerialCommands();
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