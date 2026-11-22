#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID = "sus amogus";
const char* WIFI_PASSWORD = "Z121313456";
// Non-RFID messages (the periodic sensor readings) go to the preset URL.
const char* PRESET_URL = "http://httpbin.org/post";
// RFID events go to the local ReShow server so they drive the two-scan workflow.
const char* RFID_URL = "http://172.27.168.132:8000/api/rfid/device-event";

HardwareSerial MegaLink(1);

String incomingLine;
String latestMessage;
bool discardLine = false;
unsigned long lastUpload = 0;
const unsigned long EPAPER_POLL_INTERVAL = 10000;
unsigned long lastEpaperPoll = 0;
const uint8_t MAX_PENDING_RFID_EVENTS = 4;
String pendingEvents[MAX_PENDING_RFID_EVENTS];
uint8_t pendingHead = 0;
uint8_t pendingTail = 0;
uint8_t pendingCount = 0;
bool pendingEpaperDisplay = false;
String expectedEpaperAck;
bool epaperAckReceived = false;
bool epaperAckFailed = false;
uint16_t epaperTransferId = 0;
String lastDisplayedDescription;
unsigned long epaperReadyGeneration = 0;

void readMegaMessages() {
  while (MegaLink.available()) {
    char c = MegaLink.read();
    if (c == '\n') {
      if (!discardLine && incomingLine.length() > 0) {
        if (incomingLine == expectedEpaperAck && !expectedEpaperAck.isEmpty()) {
          epaperAckReceived = true;
        } else if (incomingLine == "EPD_READY") {
          epaperReadyGeneration++;
          lastDisplayedDescription = "";
          lastEpaperPoll = millis() - EPAPER_POLL_INTERVAL;
        } else if (!expectedEpaperAck.isEmpty() &&
                   incomingLine == "EPD_ERR:" + expectedEpaperAck.substring(8)) {
          epaperAckFailed = true;
        } else if (incomingLine.indexOf("\"type\":\"rfid_event\"") >= 0 ||
                   incomingLine.indexOf("\"type\":\"wifi_test\"") >= 0) {
          if (pendingCount < MAX_PENDING_RFID_EVENTS) {
            pendingEvents[pendingTail] = incomingLine;
            pendingTail = (pendingTail + 1) % MAX_PENDING_RFID_EVENTS;
            pendingCount++;
          } else {
            Serial.println("Event queue is full; event was not queued.");
          }
        } else if (!incomingLine.startsWith("EPD_ACK:") && !incomingLine.startsWith("EPD_ERR:")) {
          latestMessage = incomingLine;
        }
      }
      incomingLine = "";
      discardLine = false;
    } else if (c != '\r' && !discardLine) {
      if (incomingLine.length() < 200) incomingLine += c;
      else discardLine = true;
    }
  }
}

bool sendEpaperPart(uint16_t id, const String &command, const String &payload) {
  readMegaMessages();
  expectedEpaperAck = "EPD_ACK:" + String(id) + ":" + command;
  epaperAckReceived = false;
  epaperAckFailed = false;
  MegaLink.print("EPD:");
  MegaLink.print(id);
  MegaLink.print(':');
  MegaLink.print(command);
  if (!payload.isEmpty()) {
    MegaLink.print(':');
    MegaLink.print(payload);
  }
  MegaLink.println();
  const unsigned long started = millis();
  while (millis() - started < 20000) {
    readMegaMessages();
    if (epaperAckReceived) {
      expectedEpaperAck = "";
      return true;
    }
    if (epaperAckFailed) {
      expectedEpaperAck = "";
      Serial.println("Mega rejected the e-paper update.");
      return false;
    }
    delay(5);
  }
  expectedEpaperAck = "";
  Serial.println("Mega did not acknowledge the e-paper update.");
  return false;
}

bool acknowledgeEpaperDescription(const String &description) {
  const String suffix = "/api/rfid/device-event";
  String url = RFID_URL;
  if (!url.endsWith(suffix)) {
    Serial.println("Cannot find e-paper delivery URL; check RFID_URL.");
    return false;
  }
  url.remove(url.length() - suffix.length());
  url += "/api/rfid/epaper-delivery";
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  if (!http.begin(client, url)) {
    Serial.println("Could not initialise e-paper delivery confirmation.");
    return false;
  }
  http.addHeader("Content-Type", "text/plain");
  int statusCode = http.POST(description);
  http.end();
  if (statusCode != 200) {
    Serial.print("E-paper delivery confirmation failed: ");
    Serial.println(statusCode);
    return false;
  }
  return true;
}

bool sendEpaperDescription(bool cardScan) {
  const String suffix = "/api/rfid/device-event";
  String url = RFID_URL;
  if (!url.endsWith(suffix)) {
    Serial.println("Cannot find e-paper description URL; check RFID_URL.");
    return false;
  }
  url.remove(url.length() - suffix.length());
  url += "/api/rfid/epaper-description";
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  if (!http.begin(client, url)) {
    Serial.println("Could not initialise e-paper description request.");
    return false;
  }
  int statusCode = http.GET();
  if (statusCode == 404) {
    http.end();
    return cardScan ? sendEpaperStatus() : true;
  }
  if (statusCode != 200) {
    Serial.print("E-paper description request failed: ");
    Serial.println(statusCode);
    http.end();
    return false;
  }
  const String description = http.getString();
  http.end();
  if (description.length() == 0 || description.length() > 180) {
    Serial.println("Invalid e-paper description length.");
    return false;
  }
  for (size_t i = 0; i < description.length(); i++) {
    if (description[i] < 32 || description[i] > 126) {
      Serial.println("Invalid e-paper description character.");
      return false;
    }
  }
  readMegaMessages();
  const unsigned long generation = epaperReadyGeneration;
  if (description != lastDisplayedDescription) {
    if (++epaperTransferId == 0) ++epaperTransferId;
    if (!sendEpaperPart(epaperTransferId, "B", String(description.length()))) return false;
    uint8_t index = 0;
    for (size_t offset = 0; offset < description.length(); offset += 24) {
      if (!sendEpaperPart(epaperTransferId, String(index++), description.substring(offset, offset + 24))) return false;
    }
    if (!sendEpaperPart(epaperTransferId, "E", "")) return false;
    readMegaMessages();
    if (generation != epaperReadyGeneration) {
      Serial.println("Mega restarted during the e-paper update; retrying.");
      return false;
    }
    lastDisplayedDescription = description;
    Serial.println("E-paper description updated from server.");
  }
  return acknowledgeEpaperDescription(description);
}

bool sendEpaperStatus() {
  const String suffix = "/api/rfid/device-event";
  String statusUrl = RFID_URL;
  if (!statusUrl.endsWith(suffix)) {
    Serial.println("Cannot find e-paper status URL; check RFID_URL.");
    return false;
  }
  statusUrl.remove(statusUrl.length() - suffix.length());
  statusUrl += "/api/rfid/epaper-status";
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  if (!http.begin(client, statusUrl)) {
    Serial.println("Could not initialise e-paper status request.");
    return false;
  }
  const int statusCode = http.GET();
  if (statusCode != 200) {
    Serial.print("E-paper status request failed: ");
    Serial.println(statusCode);
    http.end();
    return false;
  }
  const String phase = http.getString();
  http.end();
  if (phase != "idle" && phase != "pending_edit" && phase != "ready_to_confirm") {
    Serial.println("Server returned an invalid e-paper status.");
    return false;
  }
  Serial.print("Server status sent to Mega: ");
  Serial.println(phase);
  MegaLink.print("EPD:");
  MegaLink.println(phase);
  lastDisplayedDescription = "";
  return true;
}

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
  readMegaMessages();

  if (WiFi.status() == WL_CONNECTED &&
      millis() - lastEpaperPoll >= EPAPER_POLL_INTERVAL) {
    lastEpaperPoll = millis();
    sendEpaperDescription(false);
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

  const bool sendingEvent = pendingCount > 0;
  const String messageToUpload = sendingEvent
    ? pendingEvents[pendingHead]
    : latestMessage;
  if (messageToUpload.isEmpty()) {
    Serial.println("Waiting for a message from Mega.");
    return;
  }
  const bool sendingRfidEvent = sendingEvent &&
    messageToUpload.indexOf("\"type\":\"rfid_event\"") >= 0;
  const bool sendingEpaperEvent = sendingRfidEvent &&
    messageToUpload.indexOf("\"role\":\"epaper\"") >= 0;
  if (sendingEpaperEvent && pendingEpaperDisplay) {
    if (sendEpaperDescription(true)) {
      pendingHead = (pendingHead + 1) % MAX_PENDING_RFID_EVENTS;
      pendingCount--;
      pendingEpaperDisplay = false;
    }
    return;
  }
  const char* targetUrl = sendingRfidEvent ? RFID_URL : PRESET_URL;

  WiFiClient client;
  HTTPClient http;

  http.setConnectTimeout(5000);
  http.setTimeout(5000);

  if (!http.begin(client, targetUrl)) {
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
  } else {
    Serial.print("Connection error: ");
    Serial.println(HTTPClient::errorToString(statusCode));
  }

  http.end();
  if (sendingEvent && statusCode >= 200 && statusCode < 300) {
    pendingEpaperDisplay = sendingEpaperEvent;
    if (!sendingEpaperEvent || sendEpaperDescription(true)) {
      pendingHead = (pendingHead + 1) % MAX_PENDING_RFID_EVENTS;
      pendingCount--;
      pendingEpaperDisplay = false;
    }
  }
  Serial.println();
}