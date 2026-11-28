#pragma once
#include <atomic>

// UART task produces complete events and ACKs. Only the Arduino loop uses HTTP,
// writes to MegaLink, or refreshes an ESP-mounted panel.
namespace RfidBridge {
struct Event { char json[160]; };
struct Reply { char text[32]; };
struct CloudEvent {
  char id[64];
  char timestamp[28];
  char result[9];
  char role[7];
  uint32_t receivedAtMs;
};
QueueHandle_t events = nullptr, replies = nullptr, cloudEvents = nullptr;
CloudEvent pendingCloudEvent = {};
bool havePendingCloudEvent = false;
std::atomic<uint32_t> megaBoots{0};
constexpr uint32_t POLL_MS = 10000;
uint32_t nextPoll = 0, nextEvent = 0, lastDraw = 0, displayedBoot = 0;
uint32_t nextCloudEvent = 0, cloudRetryMs = 2000, cloudSequence = 0;
uint16_t transfer = 0;
uint32_t profileTransfer = 0;
bool drew = false, cardRequested = false;
String displayed;

bool lanEnabled() {
  return LOCAL_RFID_ENABLED &&
         strncmp(RFID_SERVER_ORIGIN, "http://", 7) == 0 && strlen(RFID_SERVER_ORIGIN) > 7;
}

bool networkEnabled() {
  return WIFI_SSID[0] && strncmp(WIFI_SSID, "REPLACE_", 8) &&
    strncmp(WIFI_PASSWORD, "REPLACE_", 8) && (lanEnabled() || WIFI_CARD_ECHO_ENABLED);
}

bool useEspPanel() {
  return networkEnabled() && lanEnabled() && !RFID_PANEL_ON_MEGA &&
         !ONLINE_LABELS_ENABLED && !EPAPER_BENCH_TEST;
}

uint32_t megaBootCount() {
  return megaBoots.load();
}

bool cloudConfigured() {
  return CLOUD_RFID_ENABLED && validIdentifier(DEVICE_ID) &&
         strlen(DEVICE_BEARER_TOKEN) > 0 &&
         strncmp(DEVICE_BEARER_TOKEN, "REPLACE_", 8) != 0;
}

void begin() {
  profileTransfer = esp_random();
  events = xQueueCreate(12, sizeof(Event));
  replies = xQueueCreate(8, sizeof(Reply));
  if (CLOUD_RFID_ENABLED) cloudEvents = xQueueCreate(11, sizeof(CloudEvent)); // plus one in-flight tap
  if (!events || !replies) Serial.println("RFID bridge queue allocation failed.");
  if (CLOUD_RFID_ENABLED && !cloudEvents) Serial.println("CLOUD RFID ERROR: queue allocation failed; taps cannot be uploaded.");
  if (CLOUD_RFID_ENABLED && !cloudConfigured())
    Serial.println("CLOUD RFID ERROR: configure the device ID and hardware token.");
  Serial.println("RFID events use cloud upload only; local server forwarding is disabled.");
}

void queueCloudEvent(JsonDocument &doc) {
  if (!CLOUD_RFID_ENABLED) return;
  const char *result = doc["result"] | "";
  const char *role = doc["role"] | "";
  const bool accepted = !strcmp(result, "accepted");
  if ((!accepted && strcmp(result, "denied")) ||
      (accepted && strcmp(role, "door") && strcmp(role, "epaper") && strcmp(role, "edit")) ||
      (!accepted && *role)) {
    Serial.println("CLOUD RFID ERROR: invalid tap result or role; event not queued.");
    return;
  }
  if (!cloudEvents) {
    Serial.println("CLOUD RFID ERROR: queue unavailable; tap not queued.");
    return;
  }
  CloudEvent event = {};
  snprintf(event.id, sizeof(event.id), "rfid-%s-%08lx", bootId, (unsigned long)++cloudSequence);
  strlcpy(event.result, result, sizeof(event.result));
  if (accepted) strlcpy(event.role, role, sizeof(event.role));
  event.receivedAtMs = millis();
  if (xQueueSend(cloudEvents, &event, 0) != pdTRUE)
    Serial.println("CLOUD RFID ERROR: queue full; new tap not queued.");
}

// Called only on the UART task, after the whole bounded line has arrived.
bool receive(const char *line) {
  if (!strcmp(line, "EPD_READY")) { ++megaBoots; return true; }
  if (!strncmp(line, "EPD_ACK:", 8) || !strncmp(line, "EPD_ERR:", 8) ||
      !strncmp(line, "PRO_ACK:", 8) || !strncmp(line, "PRO_ERR:", 8)) {
    Reply reply = {};
    if (strlen(line) < sizeof(reply.text) && replies) {
      strlcpy(reply.text, line, sizeof(reply.text));
      if (!xQueueSend(replies, &reply, 0)) Serial.println("Mega reply queue full.");
    }
    return true;
  }
  if (*line != '{') return false;
  JsonDocument doc;
  if (deserializeJson(doc, line)) return false;
  const char *type = doc["type"] | "";
  bool wifi = !strcmp(type, "wifi_test");
  if (!wifi && strcmp(type, "rfid_event")) return false;
  if (wifi) return true;
  Serial.printf("Mega %s\n", line);
  queueCloudEvent(doc);
  return true;
}

int request(const String &url, const char *body, const char *contentType, String *result = nullptr) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(2500);
  http.setTimeout(3500);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  if (!http.begin(client, url)) return -1;
  int code;
  if (body) {
    http.addHeader("Content-Type", contentType);
    code = http.POST((uint8_t *)body, strlen(body));
  } else {
    code = http.GET();
  }
  if (code == 200 && result) {
    OnlineLabel::BoundedResponse response;
    if (http.getSize() > 180 || http.writeToStream(&response) < 0 || response.overflow ||
        response.body.length() > 180) code = -2;
    else *result = response.body;
  }
  http.end();
  return code;
}

bool makeCloudPayload(const CloudEvent &event, char *out, size_t capacity) {
  int n;
  if (event.role[0]) {
    n = snprintf(out, capacity,
      "{\"event_id\":\"%s\",\"device_id\":\"%s\",\"source\":\"device\","
      "\"recorded_at\":\"%s\",\"result\":\"%s\",\"role\":\"%s\"}",
      event.id, DEVICE_ID, event.timestamp, event.result, event.role);
  } else {
    n = snprintf(out, capacity,
      "{\"event_id\":\"%s\",\"device_id\":\"%s\",\"source\":\"device\","
      "\"recorded_at\":\"%s\",\"result\":\"%s\"}",
      event.id, DEVICE_ID, event.timestamp, event.result);
  }
  return n > 0 && (size_t)n < capacity;
}

bool timestampCloudEvent(CloudEvent &event) {
  if (event.timestamp[0]) return true;
  struct timeval now;
  if (gettimeofday(&now, nullptr) != 0 || now.tv_sec < MIN_VALID_EPOCH) return false;
  uint32_t elapsedMs = millis() - event.receivedAtMs;
  time_t seconds = now.tv_sec - elapsedMs / 1000U;
  long microseconds = now.tv_usec - (long)(elapsedMs % 1000U) * 1000L;
  if (microseconds < 0) {
    --seconds;
    microseconds += 1000000L;
  }
  struct tm utc;
  if (!gmtime_r(&seconds, &utc)) return false;
  char date[21];
  strftime(date, sizeof(date), "%Y-%m-%dT%H:%M:%S", &utc);
  snprintf(event.timestamp, sizeof(event.timestamp), "%.19s.%03uZ",
           date, (unsigned int)(microseconds / 1000L));
  return true;
}

void tickCloud() {
  if (!CLOUD_RFID_ENABLED || !cloudEvents || (int32_t)(millis() - nextCloudEvent) < 0) return;
  if (!havePendingCloudEvent) {
    if (xQueueReceive(cloudEvents, &pendingCloudEvent, 0) != pdTRUE) return;
    havePendingCloudEvent = true;
  }
  CloudEvent &event = pendingCloudEvent;
  if (!timestampCloudEvent(event)) {
    Serial.println("CLOUD RFID ERROR: UTC clock unavailable; tap retained in RAM until time sync.");
    nextCloudEvent = millis() + 10000;
    return;
  }
  if (!cloudConfigured()) {
    Serial.println("CLOUD RFID ERROR: device ID or hardware token missing; tap retained in RAM.");
    nextCloudEvent = millis() + 60000;
    return;
  }
  char payload[256];
  if (!makeCloudPayload(event, payload, sizeof(payload))) {
    Serial.println("CLOUD RFID ERROR: payload too large; tap retained in RAM.");
    nextCloudEvent = millis() + 60000;
    return;
  }
  WiFiClientSecure tls;
  tls.setCACert(AMAZON_ROOT_CA1);
  tls.setHandshakeTimeout(8);
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  int code = -1;
  String retryAfter;
  if (http.begin(tls, String(API_ORIGIN) + "/api/rfid-events")) {
    const char *responseHeaders[] = {"Retry-After"};
    http.collectHeaders(responseHeaders, 1);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("Authorization", String("Bearer ") + DEVICE_BEARER_TOKEN);
    code = http.POST((uint8_t *)payload, strlen(payload));
    retryAfter = http.header("Retry-After");
    http.end();
  }
  if (code == 200 || code == 201) {
    Serial.printf("CLOUD RFID accepted HTTP %d: %s\n", code, event.id);
    havePendingCloudEvent = false;
    cloudRetryMs = 2000;
    nextCloudEvent = millis() + 1100;
    return;
  }
  const char *reason = "network or server error";
  if (code == 400 || code == 413 || code == 415 || code == 422) reason = "invalid event or request";
  else if (code == 401) reason = "hardware token rejected";
  else if (code == 403) reason = "device permission denied";
  else if (code == 404) reason = "route or device/showcase binding not found";
  else if (code == 409) reason = "event ID conflict";
  else if (code == 429) reason = "rate limited";
  uint32_t waitMs = cloudRetryMs;
  cloudRetryMs = cloudRetryMs < 30000 ? cloudRetryMs * 2 : 60000;
  if ((code >= 300 && code < 400) || code == 400 || code == 401 || code == 403 ||
      code == 404 || code == 409 || code == 413 || code == 415 || code == 422)
    waitMs = 60000;
  if (code == 429 && retryAfter.length()) {
    char *end = nullptr;
    unsigned long seconds = strtoul(retryAfter.c_str(), &end, 10);
    if (end != retryAfter.c_str() && *end == '\0' && seconds <= 86400UL &&
        (uint32_t)seconds * 1000UL > waitMs) waitMs = (uint32_t)seconds * 1000UL;
  }
  nextCloudEvent = millis() + waitMs;
  Serial.printf("CLOUD RFID ERROR HTTP %d: %s; %s retained in RAM, retry in %lu ms.\n",
                code, reason, event.id, (unsigned long)waitMs);
}

bool sendPart(uint32_t id, const String &command, const String &payload, uint32_t boot,
              bool profile = false) {
  String suffix = String(id) + ":" + command;
  String prefix = profile ? "PRO" : "EPD";
  String expected = prefix + "_ACK:" + suffix, error = prefix + "_ERR:" + suffix;
  if (profile) {
    char checksum[6];
    snprintf(checksum, sizeof(checksum), "*%04X", (unsigned int)crc16(expected.c_str()));
    expected += checksum;
    snprintf(checksum, sizeof(checksum), "*%04X", (unsigned int)crc16(error.c_str()));
    error += checksum;
  }
  MegaLink.print(prefix + ":" + suffix);
  if (payload.length()) MegaLink.print(":" + payload);
  MegaLink.println();
  uint32_t started = millis();
  while (millis() - started < 20000 && megaBoots.load() == boot) {
    Reply reply;
    if (xQueueReceive(replies, &reply, pdMS_TO_TICKS(25))) {
      if (expected == reply.text) return true;
      if (error == reply.text) break;
    }
  }
  Serial.printf("Mega %s did not confirm %s\n", profile ? "profile" : "display", suffix.c_str());
  return false;
}

bool sendProfile(const String &data, uint32_t boot, time_t executeBefore, bool verifyOnly = false,
                 bool verifyNotApplied = false) {
  if (!replies || !boot || megaBoots.load() != boot || !data.length() ||
      data.length() > 200 || executeBefore <= time(nullptr) + 30) {
    Serial.println("PROFILE: transfer unavailable, invalid or too close to deadline.");
    return false;
  }
  for (size_t i = 0; i < data.length(); ++i) {
    if (data[i] < 32 || data[i] > 126) {
      Serial.println("PROFILE: invalid transfer character.");
      return false;
    }
  }
  if (++profileTransfer == 0) ++profileTransfer;
  if (!sendPart(profileTransfer, "B", String(data.length()), boot, true)) return false;
  for (size_t offset = 0; offset < data.length(); offset += 24) {
    if (executeBefore <= time(nullptr) + 30 ||
        !sendPart(profileTransfer, String(offset / 24), data.substring(offset, offset + 24), boot, true)) {
      Serial.println("PROFILE: transfer interrupted or deadline too close.");
      return false;
    }
  }
  if (executeBefore <= time(nullptr) + 30 || megaBoots.load() != boot) {
    Serial.println("PROFILE: final transfer blocked by deadline or Mega reset.");
    return false;
  }
  char checksum[5];
  snprintf(checksum, sizeof(checksum), "%04X", (unsigned int)crc16(data.c_str()));
  return sendPart(profileTransfer, verifyNotApplied ? "N" : (verifyOnly ? "Q" : "E"),
                  String(checksum), boot, true) &&
         megaBoots.load() == boot;
}

bool draw(const String &text, bool status, uint32_t boot) {
  if (!RFID_PANEL_ON_MEGA) {
    LabelLayout layout;
    return useEspPanel() && makeLabelLayout(status ? "Server status" : "Exhibit", "",
                                             text.c_str(), layout) && OnlineLabel::render(layout);
  }
  if (!replies) return false;
  if (++transfer == 0) ++transfer;
  if (!sendPart(transfer, "B", String(text.length()), boot)) return false;
  for (size_t offset = 0; offset < text.length(); offset += 24) {
    if (!sendPart(transfer, String(offset / 24), text.substring(offset, offset + 24), boot)) return false;
  }
  return sendPart(transfer, "E", "", boot);
}

void pollDisplay() {
  String base = String(RFID_SERVER_ORIGIN) + "/api/rfid/epaper-";
  String text;
  int code = request(base + "description", nullptr, nullptr, &text);
  bool status = code == 404 && cardRequested;
  if (status) code = request(base + "status", nullptr, nullptr, &text);
  if (code != 200 || !text.length()) return;
  if (status && text != "idle" && text != "pending_edit" && text != "ready_to_confirm") return;
  for (size_t i = 0; i < text.length(); ++i) if (text[i] < 32 || text[i] > 126) return;
  uint32_t boot = RFID_PANEL_ON_MEGA ? megaBoots.load() : 0;
  String key = (status ? "status:" : "description:") + text;
  if (displayedBoot != boot || key != displayed) {
    if (drew && millis() - lastDraw < LABEL_MIN_REFRESH_MS) return;
    drew = true;
    lastDraw = millis();
    if (!draw(text, status, boot) || (RFID_PANEL_ON_MEGA && megaBoots.load() != boot)) return;
    displayed = key;
    displayedBoot = boot;
  }
  cardRequested = false;
  if (!status) {
    code = request(base + "delivery", text.c_str(), "text/plain");
    if (code != 200) Serial.printf("RFID display delivery receipt -> %d\n", code);
  }
}

void tick() {
  if (!networkEnabled() || !events) return;
  Event event;
  if ((int32_t)(millis() - nextEvent) >= 0 && xQueuePeek(events, &event, 0)) {
    JsonDocument doc;
    deserializeJson(doc, event.json);
    bool wifi = !strcmp(doc["type"] | "", "wifi_test");
    String url = wifi ? String("http://httpbin.org/post") : String(RFID_SERVER_ORIGIN) + "/api/rfid/device-event";
    String body;
    if (wifi) body = "{\"type\":\"wifi_test\",\"message\":\"Hello from ReShow\"}";
    else {
      if (!strcmp(doc["result"] | "", "accepted")) doc["exhibit_id"] = EXHIBIT_ID;
      serializeJson(doc, body);
    }
    int code = request(url, body.c_str(), "application/json");
    Serial.printf("%s POST -> %d\n", wifi ? "Wi-Fi test" : "RFID", code);
    bool accepted = code >= 200 && code < 300;
    // The LAN endpoint has no per-tap idempotency key. A lost response can
    // follow an applied edit. Replaying it later could reopen a finished edit.
    xQueueReceive(events, &event, 0);
    if (!accepted) Serial.println("Tap unconfirmed. Check server status before tapping again.");
    if (accepted && !strcmp(doc["role"] | "", "epaper")) {
      cardRequested = true;
      nextPoll = millis();
    }
    nextEvent = millis() + (accepted ? 100 : POLL_MS);
  }
  if (lanEnabled() && (RFID_PANEL_ON_MEGA || useEspPanel()) && (int32_t)(millis() - nextPoll) >= 0) {
    pollDisplay();
    nextPoll = millis() + POLL_MS;
  }
}
}
