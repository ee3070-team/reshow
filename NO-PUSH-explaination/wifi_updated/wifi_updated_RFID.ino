#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID = "sus amogus";
const char* WIFI_PASSWORD = "Z121313456";
const char* SERVER_URL = "http://httpbin.org/post";

HardwareSerial MegaLink(1);

String incomingLine;
String latestMessage;
bool discardLine = false;
unsigned long lastUpload = 0;
const uint8_t MAX_PENDING_RFID_EVENTS = 4;
String pendingRfidEvents[MAX_PENDING_RFID_EVENTS];
uint8_t pendingRfidHead = 0;
uint8_t pendingRfidTail = 0;
uint8_t pendingRfidCount = 0;

void setup() {
  Serial.begin(115200);
  MegaLink.setRxBufferSize(1024);
  MegaLink.begin(9600, SERIAL_8N1, 18, 17);

  // Connect to Wifi
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("Connecting to Wi-Fi...");
}

void loop() {
  // Collects data from Mega
  while (MegaLink.available()) {
    char c = MegaLink.read();

    if (c == '\n') {
      if (!discardLine && incomingLine.length() > 0) {
        Serial.print("From Mega: ");
        Serial.println(incomingLine);
        if (incomingLine.indexOf("\"type\":\"rfid_event\"") >= 0) {
          if (pendingRfidCount < MAX_PENDING_RFID_EVENTS) {
            pendingRfidEvents[pendingRfidTail] = incomingLine;
            pendingRfidTail = (pendingRfidTail + 1) % MAX_PENDING_RFID_EVENTS;
            pendingRfidCount++;
          } else {
            Serial.println("RFID event queue is full; event was not queued.");
          }
        } else {
          latestMessage = incomingLine;
        }
      }

      incomingLine = "";
      discardLine = false;
    } else if (c != '\r' && !discardLine) {
      if (incomingLine.length() < 200) {
        incomingLine += c;
      } else {
        discardLine = true;  // Ignore an oversized line.
      }
    }
  }

  // Uploads data
  if (millis() - lastUpload < 10000) {
    return;
  }
  lastUpload = millis();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi is not connected yet.");
    return;
  }

  const bool sendingRfidEvent = pendingRfidCount > 0;
  const String messageToUpload = sendingRfidEvent
    ? pendingRfidEvents[pendingRfidHead]
    : latestMessage;
  if (messageToUpload.isEmpty()) {
    Serial.println("Waiting for a message from Mega.");
    return;
  }

  WiFiClient client;
  HTTPClient http;

  http.setConnectTimeout(5000);
  http.setTimeout(5000);

  if (!http.begin(client, SERVER_URL)) {
    Serial.println("Could not initialise HTTP request.");
    return;
  }

  http.addHeader("Content-Type", "application/json");

  Serial.print("\nUploading: ");
  Serial.println(messageToUpload);

  int statusCode = http.POST(messageToUpload);

  Serial.print("HTTP status: ");
  Serial.println(statusCode);

  if (statusCode > 0) {
    Serial.println("Server response:");
    Serial.println(http.getString());
    if (sendingRfidEvent && statusCode >= 200 && statusCode < 300) {
      pendingRfidHead = (pendingRfidHead + 1) % MAX_PENDING_RFID_EVENTS;
      pendingRfidCount--;
    }
  } else {
    Serial.print("Connection error: ");
    Serial.println(HTTPClient::errorToString(statusCode));
  }

  http.end();
  Serial.println();
}