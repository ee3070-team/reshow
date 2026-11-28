#pragma once
#include <math.h>
#include "ProfileProtocol.h"

// Loop-owned durable assignment executor. A lost/ambiguous physical result stays
// fenced for reconciliation, never becomes a fabricated successful cloud receipt.
namespace OnlineProfile {
enum Phase : uint8_t { IDLE, EXECUTING, ACK_PENDING, BLOCKED };
struct Journal {
  uint32_t magic = 0x52504631;
  char device[65] = "", showcase[65] = "", lock[201] = "";
  ExhibitProfile active, target;
  uint32_t generation = 0;
  Phase phase = IDLE;
  char acknowledgement[768] = "";
};
Journal journal;
Preferences preferences;
bool ready = false;
uint32_t nextPoll = 0;
String cursor;

bool save() {
  if (preferences.putBytes("journal", &journal, sizeof(journal)) == sizeof(journal)) return true;
  ready = false;
  Serial.println("PROFILE: NVS write failed; commands stopped.");
  return false;
}
void block(const char *reason) {
  journal.phase = BLOCKED; save();
  Serial.printf("PROFILE BLOCKED: %s; retain journals and reconcile the device.\n", reason);
}
void begin() {
  if (!ONLINE_PROFILES_ENABLED) return;
  if (!validIdentifier(SHOWCASE_ID) || !strncmp(SHOWCASE_ID, "REPLACE_", 8)) {
    Serial.println("PROFILE: configure SHOWCASE_ID first."); return;
  }
  if (!preferences.begin("reshow-profile", false)) return;
  size_t size = preferences.getBytesLength("journal");
  if (size) {
    if (size != sizeof(journal) || preferences.getBytes("journal", &journal, size) != size ||
        journal.magic != 0x52504631 || journal.phase > BLOCKED ||
        journal.device[64] || journal.showcase[64] || journal.lock[200] ||
        journal.active.exhibit[64] || journal.active.tag[64] || journal.active.command[64] ||
        journal.target.exhibit[64] || journal.target.tag[64] || journal.target.command[64] ||
        journal.acknowledgement[767] || strcmp(journal.device, DEVICE_ID) ||
        strcmp(journal.showcase, SHOWCASE_ID)) {
      Serial.println("PROFILE: invalid journal/binding; stopped."); return;
    }
  } else {
    strlcpy(journal.device, DEVICE_ID, sizeof(journal.device));
    strlcpy(journal.showcase, SHOWCASE_ID, sizeof(journal.showcase));
    if (!save()) return;
  }
  ready = true;
  if (journal.phase == EXECUTING) block("Reset during profile commit; outcome uncertain");
}

bool decode(JsonObjectConst command, ExhibitProfile &p) {
  JsonObjectConst payload = command["payload"], profile = payload["profile"];
  const char *exhibit = payload["exhibit_id"] | "", *tag = profile["tag_id"] | "";
  if (!validIdentifier(exhibit) || !validIdentifier(tag) ||
      strcmp(profile["exhibit_id"] | "", exhibit) || !profile["version"].is<uint32_t>() ||
      !profile["warning_seconds"].is<uint32_t>()) return false;
  const char *fields[] = {"temperature_min_c", "temperature_max_c", "brightness_min_percent", "brightness_max_percent"};
  double values[4];
  for (uint8_t i = 0; i < 4; ++i) {
    if (!profile[fields[i]].is<double>()) return false;
    values[i] = profile[fields[i]].as<double>();
    if (!isfinite(values[i]) || values[i] < 0 || values[i] > (i < 2 ? 125 : 100)) return false;
  }
  if (values[0] >= values[1] || values[2] >= values[3] ||
      // ArduinoJson stores short decimal literals as float, including 20.1.
      // Allow its sub-millicenti rounding error, then convert to exact centi-C.
      fabs(values[0] * 100 - round(values[0] * 100)) > 0.001 ||
      fabs(values[1] * 100 - round(values[1] * 100)) > 0.001) return false;
  p.generation = command["resource_generation"];
  p.version = profile["version"];
  uint32_t seconds = profile["warning_seconds"];
  if (!p.version || p.version > 2147483647UL || !seconds || seconds > 3600) return false;
  p.temperatureMin = lround(values[0] * 100); p.temperatureMax = lround(values[1] * 100);
  p.lightMin = (uint16_t)ceil(values[2] * 1023 / 100 - 0.000000001);
  p.lightMax = (uint16_t)floor(values[3] * 1023 / 100 + 0.000000001);
  if (p.lightMin > p.lightMax) return false;
  p.holdMs = seconds * 1000UL;
  strlcpy(p.exhibit, exhibit, sizeof(p.exhibit)); strlcpy(p.tag, tag, sizeof(p.tag));
  strlcpy(p.command, command["command_id"] | "", sizeof(p.command));
  return true;
}
void acknowledge(bool success, const char *message) {
  JsonDocument ack;
  char id[40], timestamp[28];
  snprintf(id, sizeof(id), "p-%08lx%08lx%08lx", (unsigned long)esp_random(),
    (unsigned long)esp_random(), (unsigned long)esp_random());
  time_t now = time(nullptr); struct tm utc; gmtime_r(&now, &utc);
  strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
  ack["acknowledgement_id"] = id; ack["device_id"] = DEVICE_ID;
  ack["recorded_at"] = timestamp; ack["source"] = "device";
  ack["success"] = success; ack["message"] = message;
  if (measureJson(ack) >= sizeof(journal.acknowledgement)) { block("Receipt overflow"); return; }
  serializeJson(ack, journal.acknowledgement, sizeof(journal.acknowledgement));
  journal.phase = ACK_PENDING; save();
}
void execute(JsonObjectConst command) {
  const char *id = command["command_id"] | "", *lock = command["lock_id"] | "";
  uint32_t generation = command["resource_generation"] | 0U;
  time_t expires = OnlineLabel::deadline(command["execute_before"]);
  String expectedLock = String("showcase#") + SHOWCASE_ID + "#assignment";
  if (!validIdentifier(id) || strcmp(command["device_id"] | "", DEVICE_ID) ||
      strcmp(command["showcase_id"] | "", SHOWCASE_ID) || strcmp(command["type"] | "", "assignment") ||
      strcmp(command["status"] | "", "pending") || strcmp(command["protocol"] | "", "device-fence-v1") ||
      !command["resource_generation"].is<uint32_t>() || !generation || generation > 999999999 ||
      generation <= journal.generation || expectedLock != lock || expires <= time(nullptr) + 60) return;
  ExhibitProfile target;
  bool valid = decode(command, target);
  journal.generation = generation; journal.target = target;
  strlcpy(journal.target.command, id, sizeof(journal.target.command));
  strlcpy(journal.lock, lock, sizeof(journal.lock)); journal.phase = EXECUTING;
  if (!save()) return; // Durable fence before sending any physical command.
  if (!valid) { acknowledge(false, "Profile cannot be represented by Mega sensor limits."); return; }
  char wire[PROFILE_WIRE_SIZE];
  if (!formatProfile(target, wire, sizeof(wire))) { acknowledge(false, "Profile too large."); return; }
  uint32_t boot = RfidBridge::megaBoots.load();
  if (++RfidBridge::transfer == 0) ++RfidBridge::transfer;
  uint16_t transfer = RfidBridge::transfer;
  bool ok = RfidBridge::sendPart(transfer, "B", String(strlen(wire)) + "," + String(profileCrc(wire)), boot, "CFG");
  for (size_t offset = 0; ok && offset < strlen(wire); offset += 24)
    ok = RfidBridge::sendPart(transfer, String(offset / 24), String(wire).substring(offset, offset + 24), boot, "CFG");
  if (!ok) { acknowledge(false, "Profile transfer incomplete; commit was not sent."); return; }
  if (expires <= time(nullptr) + 20 || RfidBridge::megaBoots.load() != boot) {
    acknowledge(false, "Profile transfer stopped before commit."); return;
  }
  if (!RfidBridge::sendPart(transfer, "E", String(profileCrc(wire)), boot, "CFG")) {
    block("Mega commit acknowledgement missing"); return;
  }
  journal.active = target;
  acknowledge(true, "Mega persisted exhibit identity, tag and monitoring limits.");
}
void tick() {
  if (!ready || journal.phase == BLOCKED || (int32_t)(millis() - nextPoll) < 0) return;
  nextPoll = millis() + LABEL_POLL_MS;
  JsonDocument doc;
  if (journal.phase == ACK_PENDING) {
    int code = OnlineLabel::request(String("/api/commands/") + journal.target.command + "/acknowledgements", doc, journal.acknowledgement);
    if (code == 200 || code == 201) { journal.phase = IDLE; journal.acknowledgement[0] = 0; save(); }
    else if (code == 400 || code == 409 || code == 422) block("Cloud rejected receipt");
    else nextPoll = millis() + 60000;
    return;
  }
  if (ONLINE_LABELS_ENABLED && (!OnlineLabel::ready || OnlineLabel::journal.phase != OnlineLabel::IDLE)) return;
  String path = String("/api/commands?device_id=") + DEVICE_ID + "&type=assignment&status=pending&limit=1";
  if (cursor.length()) path += "&cursor=" + OnlineLabel::encodeQuery(cursor);
  int code = OnlineLabel::request(path, doc);
  if (code != 200 || !doc["items"].is<JsonArray>()) { if (code == 400) cursor = ""; nextPoll = millis() + 60000; return; }
  const char *next = doc["next_cursor"] | "";
  if (strlen(next) > 2048) return;
  cursor = next;
  JsonArrayConst items = doc["items"];
  if (items.size() != 1 || !validIdentifier(items[0]["command_id"] | "")) return;
  String exact = String("/api/commands/") + items[0]["command_id"].as<const char *>();
  doc.clear();
  if (OnlineLabel::request(exact, doc) == 200) execute(doc["item"]);
}
}

bool cloudProfileReadyForLabel(const char *exhibit, const char *assignment) {
  return OnlineProfile::ready && OnlineProfile::journal.phase == OnlineProfile::IDLE &&
    OnlineProfile::journal.active.generation != 0 &&
    megaProfileGeneration.load() == OnlineProfile::journal.active.generation &&
    (uint32_t)(millis() - megaProfileSeenAt.load()) < 30000 &&
    !strcmp(exhibit, OnlineProfile::journal.active.exhibit) &&
    !strcmp(assignment, OnlineProfile::journal.active.command);
}

bool renderCloudMega(const String &text) {
  uint32_t boot = RfidBridge::megaBoots.load();
  return RfidBridge::draw(text, false, boot) && RfidBridge::megaBoots.load() == boot;
}
