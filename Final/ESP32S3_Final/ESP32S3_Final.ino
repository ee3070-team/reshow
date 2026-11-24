/*
  ReShow: Mega_Final -> UART -> ESP32-S3 -> existing AWS HTTPS API.
  Board: ESP32S3 Dev Module, Espressif Arduino core 3.x (tested with 3.3.12).
  Display libraries: GxEPD2, Adafruit GFX, ArduinoJson 7. See ../README.md.

  Wi-Fi uses local_config.h independently of the AWS upload gate below.
  Configure the hardware token and IDs before enabling AWS uploads.
  Hardware tokens support status/readings; label routes need an additional
  backend permission change. Online labels have a separate opt-in gate.
  Do NOT use a staff Cognito token or relabel real measurements as "demo".
  No secret from the original sketches is copied into this file.
*/
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <sys/time.h>
#include <time.h>
#include <errno.h>
#include <ctype.h>

#if __has_include("local_config.h")
#include "local_config.h"
const char CONFIG_SOURCE[] = "local_config.h";
#else
#include "local_config.example.h"
const char CONFIG_SOURCE[] = "local_config.example.h";
#endif


// Enable only after the backend provisions a source=device producer for these IDs.
constexpr bool HARDWARE_UPLOADS_ENABLED = true;
const char EXHIBIT_ID[] = "helmet-001";
const char DEVICE_ID[] = "reshow-mega-01";  // Backend must authorize this ID.
const char API_ORIGIN[] = "https://urauw4ckw1.execute-api.us-east-1.amazonaws.com";
const int MEGA_RX_PIN = 18;
const int MEGA_TX_PIN = 17;  // Return link to Mega RX2 for Mega-mounted e-paper.
const size_t QUEUE_LENGTH = 24;  // ~4 minutes at 10 s/sample; RAM, not persistent.
const size_t JSON_CAPACITY = 768;
const time_t MIN_VALID_EPOCH = 1767225600;  // 2026-01-01 UTC.

// Public Amazon Root CA 1 from https://www.amazontrust.com/repository/AmazonRootCA1.pem
// Certificate trust and hostname checking stay enabled; never use setInsecure().
const char AMAZON_ROOT_CA1[] PROGMEM = R"PEM(-----BEGIN CERTIFICATE-----
MIIDQTCCAimgAwIBAgITBmyfz5m/jAo54vB4ikPmljZbyjANBgkqhkiG9w0BAQsF
ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6
b24gUm9vdCBDQSAxMB4XDTE1MDUyNjAwMDAwMFoXDTM4MDExNzAwMDAwMFowOTEL
MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv
b3QgQ0EgMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALJ4gHHKeNXj
ca9HgFB0fW7Y14h29Jlo91ghYPl0hAEvrAIthtOgQ3pOsqTQNroBvo3bSMgHFzZM
9O6II8c+6zf1tRn4SWiw3te5djgdYZ6k/oI2peVKVuRF4fn9tBb6dNqcmzU5L/qw
IFAGbHrQgLKm+a/sRxmPUDgH3KKHOVj4utWp+UhnMJbulHheb4mjUcAwhmahRWa6
VOujw5H5SNz/0egwLX0tdHA114gk957EWW67c4cX8jJGKLhD+rcdqsq08p8kDi1L
93FcXmn/6pUCyziKrlA4b9v7LWIbxcceVOF34GfID5yHI9Y/QCB/IIDEgEw+OyQm
jgSubJrIqg0CAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC
AYYwHQYDVR0OBBYEFIQYzIU07LwMlJQuCFmcx7IQTgoIMA0GCSqGSIb3DQEBCwUA
A4IBAQCY8jdaQZChGsV2USggNiMOruYou6r4lK5IpDB/G/wkjUu0yKGX9rbxenDI
U5PMCCjjmCXPI6T53iHTfIUJrU6adTrCC2qJeHZERxhlbI1Bjjt/msv0tadQ1wUs
N+gDS63pYaACbvXy8MWy7Vu33PqUXHeeE6V/Uq2V8viTO96LXFvKWlJbYK8U90vv
o/ufQJVtMVT8QtPHRh8jrdkPSHCa2XV4cdFyQzR1bldZwgJcJmApzyMZFo6IQ6XU
5MsI+yMRQ+hDKXJioaldXgjUkK642M4UwtBV8ob2xJNDd2ZhwLnoQdeXeGADbkpy
rqXRfboQnoZsG4q5WTP468SQvvG5
-----END CERTIFICATE-----
)PEM";

struct Sample {
  uint32_t sequence;
  uint32_t uptimeMs;
  int16_t temperatureCenti;
  uint16_t lightRaw;
  int16_t distanceMm;
  int8_t occupied;
  uint32_t events;
  uint8_t mode;
  bool output;
  char timestamp[28];
  char id[64];
};

HardwareSerial MegaLink(1);
QueueHandle_t sampleQueue = nullptr;
char bootId[32];
bool configurationReady = false;
bool ntpStarted = false;
bool havePending = false;
Sample pending;
uint8_t pendingStage = 0;  // 0=device status, 1=historical reading.
char pendingJson[JSON_CAPACITY];
uint32_t retryDelayMs = 2000;
uint32_t nextAttempt = 0;
bool wifiConfigured = false;
bool wifiConnectionReported = false;
uint32_t lastWifiStatus = 0;

// Explicit prototypes also keep Arduino's automatic prototype generator simple.
bool parseFrame(char *line, Sample &s);
bool makePayload(const Sample &s, bool status, char *out, size_t capacity);

uint16_t crc16(const char *text) {
  uint16_t crc = 0xFFFF;
  while (*text) {
    crc ^= (uint16_t)(uint8_t)*text++ << 8;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

bool validIdentifier(const char *value) {
  size_t length = strlen(value);
  if (length < 1 || length > 64 || !isalnum((unsigned char)value[0])) return false;
  for (size_t i = 0; i < length; ++i) {
    char c = value[i];
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '-' || c == '_')) return false;
  }
  return true;
}

#include "OnlineLabel.h"
#include "RfidBridge.h"

bool parseFrame(char *line, Sample &s) {
  char *star = strchr(line, '*');
  if (!star || strlen(star + 1) != 4) return false;
  for (uint8_t i = 1; i <= 4; ++i)
    if (!isxdigit((unsigned char)star[i])) return false;
  uint16_t suppliedCrc = (uint16_t)strtoul(star + 1, nullptr, 16);
  *star = '\0';
  if (crc16(line) != suppliedCrc || strncmp(line, "V1,", 3) != 0) return false;

  int64_t fields[9];
  char *cursor = line + 3;
  for (uint8_t i = 0; i < 9; ++i) {
    char *digits = cursor + (*cursor == '-' ? 1 : 0);
    if (*digits < '0' || *digits > '9') return false;
    errno = 0;
    char *end = nullptr;
    fields[i] = strtoll(cursor, &end, 10);
    if (errno == ERANGE || end == cursor) return false;
    if (i < 8) {
      if (*end != ',') return false;
      cursor = end + 1;
    } else if (*end != '\0') return false;
  }
  if (fields[0] < 0 || fields[0] > UINT32_MAX || fields[1] < 0 || fields[1] > UINT32_MAX ||
      (fields[2] != -32768 && (fields[2] < 0 || fields[2] > 12500)) ||
      fields[3] < 0 || fields[3] > 1023 ||
      (fields[4] != -1 && (fields[4] < 20 || fields[4] > 4000)) ||
      fields[5] < -1 || fields[5] > 1 || fields[6] < 0 || fields[6] > UINT32_MAX ||
      fields[7] < 0 || fields[7] > 2 || fields[8] < 0 || fields[8] > 1) return false;
  // No echo may mean empty (current Mega) or unknown (older Mega), never occupied.
  if (fields[4] != -1 && fields[5] == -1) return false;
  if (fields[4] == -1 && fields[5] == 1) return false;
  s.sequence = (uint32_t)fields[0];
  s.uptimeMs = (uint32_t)fields[1];
  s.temperatureCenti = (int16_t)fields[2];
  s.lightRaw = (uint16_t)fields[3];
  s.distanceMm = (int16_t)fields[4];
  s.occupied = (int8_t)fields[5];
  s.events = (uint32_t)fields[6];
  s.mode = (uint8_t)fields[7];
  s.output = fields[8] == 1;
  return true;
}

// Separate task drains UART even during DNS, TLS handshakes and HTTP timeouts.
void receiveTask(void *) {
  char line[160];
  size_t used = 0;
  bool discard = false;
  uint32_t lastByteAt = 0;
  uint32_t received = 0;
  uint32_t dropped = 0;
  for (;;) {
    while (MegaLink.available()) {
      char c = (char)MegaLink.read();
      lastByteAt = millis();
      if (c == '\r') continue;
      if (c != '\n') {
        if (!discard && used < sizeof(line) - 1) line[used++] = c;
        else discard = true;
        continue;
      }
      line[used] = '\0';
      Sample s = {};
      if (!discard && used && RfidBridge::receive(line)) {
        // Only this task reads MegaLink. HTTP and display work run in loop().
      } else if (!discard && used > 0 && parseFrame(line, s)) {
        Serial.printf("Mega seq=%lu temp=%.2fC light=%u distance_mm=%d occupied=%d events=%lu\n",
          (unsigned long)s.sequence, s.temperatureCenti / 100.0, s.lightRaw,
          s.distanceMm, s.occupied, (unsigned long)s.events);
        if (!configurationReady) {
          used = 0;
          discard = false;
          continue;
        }
        struct timeval now;
        gettimeofday(&now, nullptr);
        if (now.tv_sec >= MIN_VALID_EPOCH) {
          struct tm utc;
          time_t seconds = now.tv_sec;
          gmtime_r(&seconds, &utc);
          char date[21];
          strftime(date, sizeof(date), "%Y-%m-%dT%H:%M:%S", &utc);
          unsigned int milliseconds = (unsigned int)(now.tv_usec / 1000) % 1000U;
          snprintf(s.timestamp, sizeof(s.timestamp), "%.19s.%03uZ", date, milliseconds);
          snprintf(s.id, sizeof(s.id), "%s-%08lx", bootId, (unsigned long)++received);
          if (xQueueSend(sampleQueue, &s, 0) != pdTRUE)
            Serial.printf("Queue full: dropped NEW sample; total dropped=%lu\n", (unsigned long)++dropped);
        } else {
          Serial.println("No valid UTC clock yet: sample not queued. Waiting for Wi-Fi/NTP.");
        }
      } else if (used || discard) {
        Serial.println("Rejected UART line: CRC, length or field validation failed.");
      }
      used = 0;
      discard = false;
    }
    if ((used || discard) && (uint32_t)(millis() - lastByteAt) > 500) {
      used = 0;
      discard = false;
      Serial.println("Discarded incomplete UART line after 500 ms.");
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

bool makePayload(const Sample &s, bool status, char *out, size_t capacity) {
  char temperature[16] = "null";
  char distance[16] = "null";
  if (s.temperatureCenti != -32768)
    snprintf(temperature, sizeof(temperature), "%d.%02d", s.temperatureCenti / 100, s.temperatureCenti % 100);
  if (s.distanceMm >= 0)
    snprintf(distance, sizeof(distance), "%d.%01d", s.distanceMm / 10, s.distanceMm % 10);
  const char *occupancy = s.occupied < 0 ? "null" : (s.occupied ? "true" : "false");
  int n;
  if (status) {
    const char *mode = s.mode == 0 ? "Auto" : (s.mode == 1 ? "On" : "Off");
    n = snprintf(out, capacity,
      "{\"status_id\":\"s-%s\",\"device_id\":\"%s\",\"recorded_at\":\"%s\","
      "\"source\":\"device\",\"temperature_c\":%s,\"brightness_percent\":%.2f,"
      "\"occupied\":%s,\"distance_cm\":%s,\"lighting_mode\":\"%s\","
      "\"lighting_output\":\"%s\",\"latch\":null,\"local_control\":%s}",
      s.id, DEVICE_ID, s.timestamp, temperature, s.lightRaw * 100.0 / 1023.0,
      occupancy, distance, mode, s.output ? "On" : "Off", s.mode == 0 ? "false" : "true");
  } else {
    // ReadingInput cannot represent unknown temperature or occupancy. Do not fabricate them.
    if (s.temperatureCenti == -32768 || s.occupied < 0) return false;
    // Keep the actual Mega 10-bit ADC result. The API's 0..4095 bound includes 0..1023.
    n = snprintf(out, capacity,
      "{\"reading_id\":\"r-%s\",\"device_id\":\"%s\",\"exhibit_id\":\"%s\","
      "\"recorded_at\":\"%s\",\"temperature_c\":%s,\"brightness_raw\":%u,"
      "\"occupied\":%s,\"distance_cm\":%s,\"source\":\"device\"}",
      s.id, DEVICE_ID, EXHIBIT_ID, s.timestamp, temperature, s.lightRaw, occupancy, distance);
  }
  return n > 0 && (size_t)n < capacity;
}

void finishStage() {
  pendingJson[0] = '\0';
  retryDelayMs = 2000;
  if (++pendingStage >= 2) havePending = false;
  nextAttempt = millis() + 1100;  // Under one POST/sec, including backlog draining.
}

void handleUploadResult(int code, const String &retryAfter) {
  if (code == 200 || code == 201) {
    Serial.println("AWS accepted record (created or identical duplicate).");
    finishStage();
  } else if (code == 400 || code == 413 || code == 415 || code == 422) {
    Serial.println("Record rejected: check schema. Dropping this endpoint record, not reporting success.");
    finishStage();

  } else if (code == 409) {
    // Preserve this exact stage and its paired reading. A conflict is not an ack.
    retryDelayMs = 60000;
    nextAttempt = millis() + retryDelayMs;
    Serial.println("ID CONFLICT: pending payload and paired reading retained in RAM. Reconcile backend record; retry in 60 s. New samples may overflow the bounded queue.");
  } else {
    uint32_t waitMs = retryDelayMs;
    retryDelayMs = retryDelayMs < 30000 ? retryDelayMs * 2 : 60000;
    if (code == 401 || code == 403 || code == 404 || (code >= 300 && code < 400)) {
      waitMs = 60000;
      Serial.println("Check hardware producer permission, device/exhibit provisioning and route. Retrying in 60 s.");
    }
    // Honor Retry-After delta seconds; HTTP-date form falls back to exponential backoff.
    if (code == 429 && retryAfter.length()) {
      char *end = nullptr;
      unsigned long seconds = strtoul(retryAfter.c_str(), &end, 10);
      if (end != retryAfter.c_str() && *end == '\0' && seconds <= 86400UL) {
        uint32_t requested = (uint32_t)seconds * 1000UL;
        if (requested > waitMs) waitMs = requested;
      }
    }
    nextAttempt = millis() + waitMs;
    Serial.printf("Pending payload retained; retry after %lu ms.\n", (unsigned long)waitMs);
  }
}

void uploadPending() {
  const char *path = pendingStage == 0 ? "/api/device-status" : "/api/readings";
  WiFiClientSecure tls;
  tls.setCACert(AMAZON_ROOT_CA1);
  tls.setHandshakeTimeout(8);  // Seconds.
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  if (!http.begin(tls, String(API_ORIGIN) + path)) {
    Serial.println("HTTPS setup failed; retained pending record.");
    nextAttempt = millis() + 10000;
    return;
  }
  const char *responseHeaders[] = {"Retry-After"};
  http.collectHeaders(responseHeaders, 1);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + DEVICE_BEARER_TOKEN);
  // pendingJson and ID stay byte-for-byte identical until accepted or explicitly dropped.
  int code = http.POST((uint8_t *)pendingJson, strlen(pendingJson));
  String retryAfter = http.header("Retry-After");
  http.end();
  Serial.printf("POST %s -> %d; record %s\n", path, code, pending.id);
  handleUploadResult(code, retryAfter);
}

bool uploadConfigurationReady() {
  return HARDWARE_UPLOADS_ENABLED && strncmp(WIFI_SSID, "REPLACE_", 8) != 0 &&
    strncmp(WIFI_PASSWORD, "REPLACE_", 8) != 0 &&
    strncmp(DEVICE_BEARER_TOKEN, "REPLACE_", 8) != 0 && strlen(DEVICE_BEARER_TOKEN) > 0 &&
    strncmp(EXHIBIT_ID, "REPLACE_", 8) != 0 && validIdentifier(DEVICE_ID) && validIdentifier(EXHIBIT_ID);
}

// Joining the hotspot does not require an AWS token or an enabled upload gate.
bool wifiConfigurationReady() {
  const size_t ssidLength = strlen(WIFI_SSID);
  return ssidLength > 0 && ssidLength <= 32 && strlen(WIFI_PASSWORD) <= 64 &&
    strncmp(WIFI_SSID, "REPLACE_", 8) != 0 &&
    strncmp(WIFI_PASSWORD, "REPLACE_", 8) != 0;
}

void wifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  // Wi-Fi events run on a separate task. Only log; no shared state is changed.
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      Serial.println("WIFI ASSOCIATED: joined hotspot; waiting for an IP address.");
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.println("WIFI GOT_IP: hotspot connection and DHCP succeeded.");
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      Serial.printf("WIFI DISCONNECTED: reason=%u. Auto-reconnect is enabled.\n",
                    (unsigned int)info.wifi_sta_disconnected.reason);
      break;
    case ARDUINO_EVENT_WIFI_STA_LOST_IP:
      Serial.println("WIFI LOST_IP: waiting for a new IP address.");
      break;
    default:
      break;
  }
}

void startWifi() {
  wifiConfigured = wifiConfigurationReady();
  if (!wifiConfigured) {
    Serial.println("WIFI NOT CONFIGURED: set SSID/password in local_config.h and upload again.");
    return;
  }
  // Same startup sequence as the working WIFI.ino. Let the core handle retries.
  WiFi.onEvent(wifiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  Serial.println("WIFI START: connecting with the configured credentials.");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

bool serviceWifi() {
  if (!wifiConfigured) return false;
  const wl_status_t status = WiFi.status();
  if (status == WL_CONNECTED) {
    if (!wifiConnectionReported) {
      wifiConnectionReported = true;
      Serial.printf("WIFI CONNECTED: IP=%s RSSI=%ld dBm\n",
                    WiFi.localIP().toString().c_str(), (long)WiFi.RSSI());
    }
    return true;
  }
  wifiConnectionReported = false;
  const uint32_t now = millis();
  if ((uint32_t)(now - lastWifiStatus) >= 5000) {
    lastWifiStatus = now;
    Serial.printf("WIFI WAIT: status=%d; watch ASSOCIATED/GOT_IP/DISCONNECTED events.\n",
                  (int)status);
  }
  // Do not force reconnect every 15 seconds while association/DHCP is underway.
  return false;
}

void setup() {
  Serial.begin(115200);  // No while(!Serial): operate without a USB monitor attached.
  setenv("TZ", "UTC0", 1);
  tzset();
  sampleQueue = xQueueCreate(QUEUE_LENGTH, sizeof(Sample));
  if (!sampleQueue) {
    Serial.println("FATAL: cannot allocate sample queue.");
    while (true) delay(1000);
  }
  MegaLink.setRxBufferSize(2048);
  MegaLink.begin(9600, SERIAL_8N1, MEGA_RX_PIN, MEGA_TX_PIN);
  RfidBridge::begin();
  startWifi();
  configurationReady = wifiConfigured && uploadConfigurationReady();
  // Chip ID + 64 random bits + receive counter avoids reuse across resets.
  uint64_t mac = ESP.getEfuseMac();
  snprintf(bootId, sizeof(bootId), "%04lx%08lx-%08lx%08lx",
    (unsigned long)((mac >> 32) & 0xFFFF), (unsigned long)(mac & 0xFFFFFFFF),
    (unsigned long)esp_random(), (unsigned long)esp_random());
  if (xTaskCreate(receiveTask, "MegaRX", 4096, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("FATAL: cannot start UART receiver.");
    while (true) delay(1000);
  }
  OnlineLabel::begin();
  if (RfidBridge::useEspPanel()) SPI.begin(EPD_SCK, -1, EPD_MOSI, EPD_CS);
  Serial.println("ReShow ESP32-S3 ready; Mega RX=GPIO18, UART=9600, USB=115200.");
}

void loop() {
  static uint32_t lastNotice = 0;
  static bool bootNoticePrinted = false;
  uint32_t now = millis();
  if (!bootNoticePrinted && now >= 2000) {
    bootNoticePrinted = true;
    Serial.printf("ReShow WiFi v2 | config=%s | WiFi credentials=%s | AWS flag=%s | AWS config=%s\n",
      CONFIG_SOURCE, wifiConfigured ? "loaded" : "missing/invalid",
      HARDWARE_UPLOADS_ENABLED ? "ON" : "OFF", configurationReady ? "ready" : "disabled/incomplete");
    Serial.printf("WiFi status=%d (3=connected). Credentials are never printed.\n", (int)WiFi.status());
  }
  if (!wifiConfigured) {
    if ((uint32_t)(now - lastNotice) >= 10000) {
      lastNotice = now;
      Serial.println("WIFI NOT CONFIGURED: edit local_config.h and upload this sketch again. UART reception is running.");
    }
    delay(10);
    return;
  }
  if (!serviceWifi()) {
    delay(10);
    return;
  }
  RfidBridge::tick();
  if ((configurationReady || CLOUD_RFID_ENABLED) && !ntpStarted) {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    ntpStarted = true;
    Serial.println("Wi-Fi connected; synchronizing UTC clock for TLS and timestamps.");
  }
  if ((configurationReady || CLOUD_RFID_ENABLED) && time(nullptr) < MIN_VALID_EPOCH) {
    if ((uint32_t)(now - lastNotice) >= 10000) {
      lastNotice = now;
      Serial.println("Waiting for UTC time: check internet/DNS and UDP port 123.");
    }
    delay(10);
    return;
  }
  RfidBridge::tickCloud();
  if (!configurationReady) {
    if ((uint32_t)(now - lastNotice) >= 10000) {
      lastNotice = now;
      Serial.println("WIFI CONNECTED; AWS telemetry disabled: check HARDWARE_UPLOADS_ENABLED, device token and IDs. UART reception is running.");
    }
    delay(10);
    return;
  }
  OnlineLabel::tick();  // UART task continues during HTTPS/full e-paper refresh.
  now = millis();
  if ((int32_t)(now - nextAttempt) < 0) {
    delay(10);
    return;
  }
  if (!havePending) {
    if (xQueueReceive(sampleQueue, &pending, 0) != pdTRUE) {
      delay(10);
      return;
    }
    havePending = true;
    pendingStage = 0;
    pendingJson[0] = '\0';
  }
  if (!pendingJson[0] && !makePayload(pending, pendingStage == 0, pendingJson, sizeof(pendingJson))) {
    Serial.println("Endpoint record skipped: missing required measurement or payload too large.");
    finishStage();
    return;
  }
  uploadPending();
}
