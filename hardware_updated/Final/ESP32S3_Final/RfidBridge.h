#pragma once
#include <atomic>

// UART task produces complete events and ACKs. Only the Arduino loop uses HTTP,
// writes to MegaLink, or refreshes an ESP-mounted panel.
namespace RfidBridge {
struct Event { char json[256]; };
struct Reply { char text[32]; };
QueueHandle_t events = nullptr, replies = nullptr;
std::atomic<uint32_t> megaBoots{0};
constexpr uint32_t POLL_MS = 10000;
uint32_t nextPoll = 0, nextEvent = 0, lastDraw = 0, displayedBoot = 0;
uint16_t transfer = 0;
bool drew = false, cardRequested = false;
String displayed;

bool lanEnabled() {
  return strncmp(RFID_SERVER_ORIGIN, "http://", 7) == 0 && strlen(RFID_SERVER_ORIGIN) > 7;
}

bool networkEnabled() {
  return WIFI_SSID[0] && strncmp(WIFI_SSID, "REPLACE_", 8) &&
    strncmp(WIFI_PASSWORD, "REPLACE_", 8) && (lanEnabled() || WIFI_CARD_ECHO_ENABLED);
}

bool useEspPanel() {
  return networkEnabled() && lanEnabled() && !RFID_PANEL_ON_MEGA &&
         !(ONLINE_LABELS_ENABLED && strncmp(SHOWCASE_ID, "REPLACE_", 8)) && !EPAPER_BENCH_TEST;
}

void begin() {
  events = xQueueCreate(12, sizeof(Event));
  replies = xQueueCreate(8, sizeof(Reply));
  if (!events || !replies) Serial.println("RFID bridge queue allocation failed.");
}

// Called only on the UART task, after the whole bounded line has arrived.
bool receive(const char *line) {
  if (!strcmp(line, "EPD_READY")) { ++megaBoots; megaProfileGeneration.store(0); megaProfileSeenAt.store(0); return true; }
  if (!strncmp(line, "EPD_ACK:", 8) || !strncmp(line, "EPD_ERR:", 8) ||
      !strncmp(line, "CFG_ACK:", 8) || !strncmp(line, "CFG_ERR:", 8)) {
    Reply reply = {};
    if (strlen(line) < sizeof(reply.text) && replies) {
      strlcpy(reply.text, line, sizeof(reply.text));
      if (!xQueueSend(replies, &reply, 0)) Serial.println("E-paper reply queue full.");
    }
    return true;
  }
  if (*line != '{') return false;
  JsonDocument doc;
  if (deserializeJson(doc, line)) return false;
  const char *type = doc["type"] | "";
  bool wifi = !strcmp(type, "wifi_test");
  if (!wifi && strcmp(type, "rfid_event")) return false;
  Serial.printf("Mega %s\n", line);
  if (!networkEnabled() || (wifi ? !WIFI_CARD_ECHO_ENABLED : !lanEnabled())) return true;
  Event event = {};
  if (strlen(line) >= sizeof(event.json)) return false;
  strlcpy(event.json, line, sizeof(event.json));
  if (!events || !xQueueSend(events, &event, 0)) Serial.println("RFID queue full; dropped new tap.");
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

bool sendPart(uint16_t id, const String &command, const String &payload, uint32_t boot, const char *prefix = "EPD") {
  String suffix = String(id) + ":" + command;
  String expected = String(prefix) + "_ACK:" + suffix, error = String(prefix) + "_ERR:" + suffix;
  // Bind a profile commit receipt to its content, even if a transfer ID is reused.
  if (!strcmp(prefix, "CFG") && command == "E") {
    expected += ":" + payload; error += ":" + payload;
  }
  MegaLink.print(String(prefix) + ":" + suffix);
  if (payload.length()) MegaLink.print(":" + payload);
  MegaLink.println();
  uint32_t started = millis();
  while (millis() - started < (command == "E" ? 15000UL : 2000UL) && megaBoots.load() == boot) {
    Reply reply;
    if (xQueueReceive(replies, &reply, pdMS_TO_TICKS(25))) {
      if (expected == reply.text) return true;
      if (error == reply.text) break;
    }
  }
  Serial.printf("Mega display did not confirm %s\n", suffix.c_str());
  return false;
}

bool draw(const String &text, bool status, uint32_t boot) {
  if (!RFID_PANEL_ON_MEGA) {
    LabelLayout layout;
    return useEspPanel() && makeLabelLayout(status ? "Server status" : "Exhibit", "",
                                             text.c_str(), layout) && OnlineLabel::render(layout);
  }
  if (!replies) return false;
  if (++transfer == 0) ++transfer;
  if (!sendPart(transfer, "B", String(text.length()) + "," + String(profileCrc(text.c_str())), boot)) return false;
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
      // Preserve the Mega item ID captured at tap time, including queued taps.
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
  if (!(ONLINE_LABELS_ENABLED && strncmp(SHOWCASE_ID, "REPLACE_", 8)) && lanEnabled() && (RFID_PANEL_ON_MEGA || useEspPanel()) && (int32_t)(millis() - nextPoll) >= 0) {
    pollDisplay();
    nextPoll = millis() + POLL_MS;
  }
}
}
