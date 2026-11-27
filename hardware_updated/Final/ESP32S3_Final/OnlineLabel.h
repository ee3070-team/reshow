#pragma once
#include <ArduinoJson.h>
#include <Preferences.h>
#include <SPI.h>
#include <GxEPD2_BW.h>
#include "DisplayConfig.h"
#include "LabelLayout.h"

// Included after API_ORIGIN, DEVICE_ID, EXHIBIT_ID, CA and validIdentifier().
// All cloud/display state is owned by the Arduino loop, never the UART task.
namespace OnlineLabel {
GxEPD2_BW<GxEPD2_154_D67, GxEPD2_154_D67::HEIGHT> display(
  GxEPD2_154_D67(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));
Preferences preferences;
enum Phase : uint8_t { IDLE, EXECUTING, ACK_PENDING, BLOCKED };
struct Journal {
  uint32_t magic;
  char device[65], showcase[65], exhibit[65];
  char lock[201], command[65];
  uint32_t generation;
  Phase phase;
  char acknowledgement[768];
};
Journal journal = {};
constexpr uint32_t JOURNAL_MAGIC = 0x524C4231;
bool ready = false;
uint32_t nextPoll = 0, lastRefresh = 0;
bool refreshed = false;
String cursor;
bool render(const LabelLayout &layout);

bool saveJournal() {
  // One NVS blob atomically contains the fence, execution phase and exact retry body.
  if (preferences.putBytes("journal", &journal, sizeof(journal)) != sizeof(journal)) {
    ready = false;
    Serial.println("LABEL: NVS write failed; executor stopped.");
    return false;
  }
  return true;
}

void block(const char *reason) {
  Serial.printf("LABEL BLOCKED: %s. See the recovery notes linked from README.md.\n", reason);
  journal.phase = BLOCKED;
  saveJournal();
}

void begin() {
  if (EPAPER_BENCH_TEST) {
    if (ONLINE_LABELS_ENABLED) {
      Serial.println("LABEL: bench test and online mode cannot run together.");
      return;
    }
    SPI.begin(EPD_SCK, -1, EPD_MOSI, EPD_CS);
    LabelLayout layout;
    makeLabelLayout("ReShow display test", "GDEH0154D67 200x200",
                    "Offline wiring test.\nNo AWS acknowledgement is sent.", layout);
    Serial.println(render(layout) ? "LABEL TEST: BUSY cycle completed. Inspect all text/border."
                                 : "LABEL TEST FAILED: check power, SPI and BUSY wiring.");
    return;
  }
  if (!ONLINE_LABELS_ENABLED) return;
  if (!validIdentifier(SHOWCASE_ID) || strncmp(SHOWCASE_ID, "REPLACE_", 8) == 0) {
    Serial.println("LABEL: configure SHOWCASE_ID first.");
    return;
  }
  if (!preferences.begin("reshow-label", false)) return;
  size_t size = preferences.getBytesLength("journal");
  if (size) {
    if (size != sizeof(journal) ||
        preferences.getBytes("journal", &journal, sizeof(journal)) != sizeof(journal) ||
        journal.magic != JOURNAL_MAGIC || journal.phase > BLOCKED ||
        journal.device[64] || journal.showcase[64] || journal.exhibit[64] ||
        journal.command[64] || journal.lock[200] || journal.acknowledgement[767] ||
        strcmp(journal.device, DEVICE_ID) || strcmp(journal.showcase, SHOWCASE_ID)) {
      Serial.println("LABEL: invalid journal or changed device binding; stopped. Do not erase fences blindly.");
      return;
    }
  } else {
    journal.magic = JOURNAL_MAGIC;
    strlcpy(journal.device, DEVICE_ID, sizeof(journal.device));
    strlcpy(journal.showcase, SHOWCASE_ID, sizeof(journal.showcase));
    strlcpy(journal.exhibit, EXHIBIT_ID, sizeof(journal.exhibit));
    if (!saveJournal()) return;
  }
  ready = true;
  if (journal.phase == EXECUTING) block("Reset during a display operation; physical result is uncertain");
  if (!RFID_PANEL_ON_MEGA) SPI.begin(EPD_SCK, -1, EPD_MOSI, EPD_CS);
  // Do not clear or refresh on boot: e-paper retains its existing image without power.
  // Conservatively enforce a cooldown after every restart as well.
  refreshed = true;
  lastRefresh = millis();
}

class BoundedResponse : public Stream {
 public:
  String body;
  bool overflow = false;
  static constexpr size_t LIMIT = 16384;
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t *data, size_t count) override {
    if (body.length() + count > LIMIT || !body.concat((const char *)data, count)) {
      overflow = true; return 0;
    }
    return count;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}
};

int request(const String &path, JsonDocument &document, const char *body = nullptr) {
  WiFiClientSecure client;
  client.setCACert(AMAZON_ROOT_CA1);
  client.setHandshakeTimeout(10);
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  if (!http.begin(client, String(API_ORIGIN) + path)) return -1;
  http.addHeader("Authorization", String("Bearer ") + DEVICE_BEARER_TOKEN);
  int code;
  if (body) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST((uint8_t *)body, strlen(body));
  } else code = http.GET();
  if (!body && code == 200) {
    BoundedResponse response;
    if (http.getSize() > (int)BoundedResponse::LIMIT ||
        http.writeToStream(&response) < 0 || response.overflow ||
        deserializeJson(document, response.body, DeserializationOption::NestingLimit(12))) code = -2;
  }
  http.end();
  Serial.printf("LABEL %s -> %d\n", body ? "ACK" : "GET", code);
  return code;
}

String encodeQuery(const String &text) {
  String encoded;
  const char hex[] = "0123456789ABCDEF";
  for (size_t i = 0; i < text.length(); ++i) {
    uint8_t c = text[i];
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') encoded += (char)c;
    else { encoded += '%'; encoded += hex[c >> 4]; encoded += hex[c & 15]; }
  }
  return encoded;
}

// Accept the API's UTC timestamp with optional fractional seconds. Rounding down
// plus a 30-second margin leaves time for the full refresh and acknowledgement.
time_t deadline(const char *value) {
  if (!value || strlen(value) < 20 || strlen(value) > 27) return 0;
  struct tm utc = {};
  char *end = strptime(value, "%Y-%m-%dT%H:%M:%S", &utc);
  if (!end) return 0;
  if (*end == '.') {
    ++end;
    unsigned digits = 0;
    while (isdigit((unsigned char)*end)) { ++end; ++digits; }
    if (digits < 1 || digits > 6) return 0;
  }
  if (strcmp(end, "Z")) return 0;
  // ESP32 core's UTC conversion; TZ is set explicitly in the main setup.
  return mktime(&utc);
}

bool busySeen = false, busyFault = false;
uint32_t refreshStarted = 0;
void monitorBusy(const void *) {
  busySeen = true;
  if ((uint32_t)(millis() - refreshStarted) >= 9000UL) busyFault = true;
  delay(1);  // Let the UART receive task continue during the waveform.
}

bool render(const LabelLayout &layout) {
  busySeen = busyFault = false;
  refreshStarted = millis();
  display.epd2.setBusyCallback(monitorBusy);
  // 2ms reset pulse for Waveshare boards with the reset/power circuit.
  display.init(0, true, 2, false);
  display.setRotation(0);
  display.setFont(nullptr);
  display.setTextColor(GxEPD_BLACK);
  display.setTextSize(1);
  display.setTextWrap(false);
  display.fillScreen(GxEPD_WHITE);
  display.drawRect(0, 0, 200, 200, GxEPD_BLACK);
  for (uint8_t i = 0; i < layout.used; ++i) {
    display.setCursor(7, 8 + i * 8);
    display.print(layout.lines[i]);
  }
  display.drawFastHLine(7, 11 + layout.ruleAfter * 8, 186, GxEPD_BLACK);
  display.setCursor(7, 188);
  display.print("ReShow");
  display.display(false);  // Full buffer, full refresh. Never refresh on every poll.
  bool ok = busySeen && !busyFault && digitalRead(EPD_BUSY) == LOW;
  display.hibernate();
  lastRefresh = millis();
  refreshed = true;
  return ok && !busyFault;
}

bool plainJsonText(JsonVariantConst value) {
  if (!value.is<const char *>()) return false;
  JsonString text = value.as<JsonString>();
  // An escaped NUL must not turn a longer cloud string into a shorter display.
  return text.size() == strlen(text.c_str());
}

void prepareAcknowledgement(bool success, const char *message) {
  JsonDocument ack;
  char id[40], timestamp[28];
  snprintf(id, sizeof(id), "a-%08lx%08lx%08lx", (unsigned long)esp_random(),
           (unsigned long)esp_random(), (unsigned long)esp_random());
  time_t now = time(nullptr);
  struct tm utc;
  gmtime_r(&now, &utc);
  strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
  ack["acknowledgement_id"] = id;
  ack["device_id"] = DEVICE_ID;
  ack["recorded_at"] = timestamp;
  ack["source"] = "device";
  ack["success"] = success;
  ack["message"] = message;
  if (measureJson(ack) >= sizeof(journal.acknowledgement)) { block("ACK too large"); return; }
  serializeJson(ack, journal.acknowledgement, sizeof(journal.acknowledgement));
  journal.phase = ACK_PENDING;
  saveJournal();
}

void execute(JsonObjectConst command) {
  const char *id = command["command_id"] | "";
  const char *lock = command["lock_id"] | "";
  uint32_t generation = command["resource_generation"] | 0U;
  if (!validIdentifier(id) || strcmp(command["device_id"] | "", DEVICE_ID) ||
      strcmp(command["showcase_id"] | "", SHOWCASE_ID) ||
      strcmp(command["type"] | "", "label") || strcmp(command["status"] | "", "pending") ||
      strcmp(command["protocol"] | "", "device-fence-v1") ||
      !command["resource_generation"].is<uint32_t>() || generation == 0 || generation > 999999999 ||
      !*lock || strlen(lock) > 200 || generation <= journal.generation ||
      deadline(command["execute_before"]) <= time(nullptr) + 30) return;
  if (*journal.lock && strcmp(lock, journal.lock)) { block("Label lock changed"); return; }
  JsonObjectConst payload = command["payload"];
  if (!cloudProfileReadyForLabel(payload["exhibit_id"] | "", payload["assignment_command_id"] | "")) return;
  if (!validIdentifier(payload["assignment_command_id"] | "") ||
      !payload["draft_version"].is<uint32_t>() || payload["draft_version"].as<uint32_t>() == 0) return;
  JsonObjectConst content = payload["content"];
  LabelLayout layout;
  bool fits = plainJsonText(content["title"]) && plainJsonText(content["collection"]) &&
    plainJsonText(content["description"]) && content["image_media_id"].isNull() &&
    makeLabelLayout(content["title"], content["collection"], content["description"], layout);
  String megaText = String(content["title"] | "");
  const char *collection = content["collection"] | "";
  if (*collection) megaText += String(" | ") + collection;
  megaText += String(" | ") + (content["description"] | "");
  if (RFID_PANEL_ON_MEGA) {
    fits = plainJsonText(content["title"]) && plainJsonText(content["collection"]) &&
      plainJsonText(content["description"]) && content["image_media_id"].isNull() &&
      megaText.length() > 0 && megaText.length() <= 180;
    for (size_t i = 0; fits && i < megaText.length(); ++i)
      if (megaText[i] < 32 || megaText[i] > 126) fits = false;
  }
  strlcpy(journal.exhibit, payload["exhibit_id"] | "", sizeof(journal.exhibit));
  strlcpy(journal.lock, lock, sizeof(journal.lock));
  strlcpy(journal.command, id, sizeof(journal.command));
  journal.generation = generation;
  journal.phase = EXECUTING;
  if (!saveJournal()) return;  // Fence is durable BEFORE any display side effect.
  if (!fits) {
    prepareAcknowledgement(false, "Text-only 200x200 label: use printable ASCII, remove image, shorten text to fit.");
    return;
  }
  bool rendered = RFID_PANEL_ON_MEGA ? renderCloudMega(megaText) : render(layout);
  if (RFID_PANEL_ON_MEGA) { refreshed = true; lastRefresh = millis(); }
  if (!rendered) {
    // BUSY faults are ambiguous: pixels might be partially changed. Never claim
    // that the previous label is intact or that the new one has been applied.
    block("E-paper BUSY missing/timeout; display outcome uncertain");
    return;
  }
  prepareAcknowledgement(true, "GDEH0154D67 full refresh completed; BUSY cycle observed.");
}

void tick() {
  if (!ready || journal.phase == BLOCKED || (int32_t)(millis() - nextPoll) < 0) return;
  nextPoll = millis() + LABEL_POLL_MS;
  JsonDocument document;
  if (journal.phase == ACK_PENDING) {
    int code = request(String("/api/commands/") + journal.command + "/acknowledgements",
                       document, journal.acknowledgement);
    if (code == 200 || code == 201) {
      journal.phase = IDLE;
      journal.acknowledgement[0] = '\0';
      saveJournal();
    } else if (code == 400 || code == 409 || code == 422) block("ACK rejected; reconcile physical label with backend");
    else nextPoll = millis() + 60000UL;  // Keep exact durable receipt for retry/reset.
    return;
  }
  if (refreshed && (uint32_t)(millis() - lastRefresh) < LABEL_MIN_REFRESH_MS) return;
  String path = String("/api/commands?device_id=") + DEVICE_ID + "&type=label&status=pending&limit=1";
  if (cursor.length()) path += "&cursor=" + encodeQuery(cursor);
  int code = request(path, document);
  if (code != 200 || !document["items"].is<JsonArray>()) {
    if (code == 400) cursor = "";
    nextPoll = millis() + 60000UL;
    return;
  }
  const char *next = document["next_cursor"] | "";
  if (strlen(next) > 2048) { nextPoll = millis() + 60000UL; return; }
  cursor = next;  // Includes empty filtered pages with a continuation cursor.
  JsonArrayConst items = document["items"];
  if (items.size() != 1) return;
  const char *id = items[0]["command_id"] | "";
  if (!validIdentifier(id)) return;
  String exact = String("/api/commands/") + id;
  document.clear();
  if (request(exact, document) == 200) execute(document["item"]);
}
}  // namespace OnlineLabel
