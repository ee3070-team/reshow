#pragma once
#include "AssignmentProfile.h"

namespace OnlineAssignment {
enum Phase : uint8_t { IDLE, EXECUTING, ACK_PENDING, BLOCKED };
struct Journal {
  uint32_t magic;
  char device[65], showcase[65], lock[201], command[65], exhibit[65];
  char profile[201], acknowledgement[512];
  uint32_t generation;
  Phase phase;
};
constexpr uint32_t JOURNAL_MAGIC = 0x52415331;
Preferences preferences;
Journal journal = {};
bool ready = false;
uint32_t verifiedBoot = 0, nextPoll = 0;
bool pollingRecovery = true;
String cursor;

bool saveJournal() {
  if (preferences.putBytes("journal", &journal, sizeof(journal)) != sizeof(journal)) {
    ready = false;
    Serial.println("ASSIGNMENT ERROR: NVS write failed; executor stopped.");
    return false;
  }
  return true;
}

void block(const char *reason) {
  verifiedBoot = 0;
  journal.phase = BLOCKED;
  Serial.printf("ASSIGNMENT BLOCKED: %s. Reconcile with the backend owner.\n", reason);
  saveJournal();
}

void begin() {
  if (!ONLINE_ASSIGNMENTS_ENABLED) return;
  if (EPAPER_BENCH_TEST || !validIdentifier(DEVICE_ID) || !validIdentifier(SHOWCASE_ID) ||
      !strncmp(SHOWCASE_ID, "REPLACE_", 8)) {
    Serial.println("ASSIGNMENT ERROR: check device, showcase and bench-test settings.");
    return;
  }
  if (!preferences.begin("reshow-assign", false)) {
    Serial.println("ASSIGNMENT ERROR: cannot open NVS journal.");
    return;
  }
  size_t size = preferences.getBytesLength("journal");
  if (size) {
    if (size != sizeof(journal) ||
        preferences.getBytes("journal", &journal, sizeof(journal)) != sizeof(journal) ||
        journal.magic != JOURNAL_MAGIC || journal.phase > BLOCKED ||
        journal.device[64] || journal.showcase[64] || journal.lock[200] ||
        journal.command[64] || journal.exhibit[64] || journal.profile[200] ||
        journal.acknowledgement[511] || strcmp(journal.device, DEVICE_ID) ||
        strcmp(journal.showcase, SHOWCASE_ID) ||
        (journal.phase == ACK_PENDING && (!journal.command[0] || !journal.profile[0] ||
                                          !journal.acknowledgement[0]))) {
      Serial.println("ASSIGNMENT ERROR: invalid journal or changed binding; do not erase fences.");
      return;
    }
  } else {
    journal.magic = JOURNAL_MAGIC;
    strlcpy(journal.device, DEVICE_ID, sizeof(journal.device));
    strlcpy(journal.showcase, SHOWCASE_ID, sizeof(journal.showcase));
    if (!saveJournal()) return;
  }
  ready = true;
  if (journal.phase == EXECUTING)
    block("Reset during profile application; the physical result is unknown");
}

bool labelReady() {
  return ready && journal.phase == IDLE && journal.profile[0] && verifiedBoot &&
         verifiedBoot == RfidBridge::megaBootCount();
}

bool labelBindingMatches(const char *exhibit, const char *assignment) {
  return labelReady() && !strcmp(exhibit, journal.exhibit) && !strcmp(assignment, journal.command);
}

bool verifyProfile() {
  uint32_t boot = RfidBridge::megaBootCount();
  if (!boot || !journal.profile[0]) return false;
  if (verifiedBoot == boot) return true;
  verifiedBoot = 0;
  if (!RfidBridge::sendProfile(String(journal.profile), boot, time(nullptr) + 120, true))
    return false;
  if (RfidBridge::megaBootCount() != boot) return false;
  verifiedBoot = boot;
  Serial.println("ASSIGNMENT: Mega verified its persisted active profile.");
  return true;
}

void prepareAcknowledgement() {
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
  ack["success"] = true;
  ack["message"] = "Mega saved and activated the monitoring profile.";
  if (measureJson(ack) >= sizeof(journal.acknowledgement)) {
    block("Receipt too large");
    return;
  }
  serializeJson(ack, journal.acknowledgement, sizeof(journal.acknowledgement));
  journal.phase = ACK_PENDING;
  saveJournal();
}

bool serialProfileForCommand(JsonObjectConst command, char *out, size_t capacity) {
  const char *id = command["command_id"] | "";
  JsonObjectConst payload = command["payload"];
  JsonObjectConst profile = payload["profile"];
  const char *exhibit = payload["exhibit_id"] | "";
  uint32_t generation = command["resource_generation"] | 0U;
  if (!OnlineLabel::plainJsonText(command["command_id"]) || !validIdentifier(id) ||
      !command["resource_generation"].is<uint32_t>() || !generation || generation > 999999999UL ||
      !OnlineLabel::plainJsonText(command["exhibit_id"]) ||
      !OnlineLabel::plainJsonText(payload["exhibit_id"]) || !validIdentifier(exhibit) ||
      strcmp(command["exhibit_id"] | "", exhibit) ||
      !OnlineLabel::plainJsonText(profile["exhibit_id"]) ||
      strcmp(profile["exhibit_id"] | "", exhibit) ||
      !profile["version"].is<uint32_t>() || !profile["version"].as<uint32_t>() ||
      profile["version"].as<uint32_t>() > 2147483647UL ||
      !profile["temperature_min_c"].is<double>() || !profile["temperature_max_c"].is<double>() ||
      !profile["brightness_min_percent"].is<double>() ||
      !profile["brightness_max_percent"].is<double>() || !profile["warning_seconds"].is<uint32_t>())
    return false;
  AssignmentLimits limits;
  if (!assignmentLimits(profile["temperature_min_c"].as<double>(),
                        profile["temperature_max_c"].as<double>(),
                        profile["brightness_min_percent"].as<double>(),
                        profile["brightness_max_percent"].as<double>(),
                        profile["warning_seconds"].as<uint32_t>(), limits))
    return false;
  int length = snprintf(out, capacity, "%lu,%lu,%s,%s,%d,%d,%u,%u,%u",
    (unsigned long)generation, (unsigned long)profile["version"].as<uint32_t>(), exhibit, id,
    (int)limits.temperatureMin, (int)limits.temperatureMax, (unsigned)limits.lightMin,
    (unsigned)limits.lightMax, (unsigned)limits.warningSeconds);
  return length > 0 && (size_t)length < capacity;
}

void execute(JsonObjectConst command) {
  const char *id = command["command_id"] | "";
  const char *lock = command["lock_id"] | "";
  uint32_t generation = command["resource_generation"] | 0U;
  String expectedLock = String("showcase#") + SHOWCASE_ID + "#assignment";
  if (!OnlineLabel::plainJsonText(command["command_id"]) ||
      !OnlineLabel::plainJsonText(command["device_id"]) ||
      !OnlineLabel::plainJsonText(command["showcase_id"]) ||
      !OnlineLabel::plainJsonText(command["lock_id"]) ||
      !OnlineLabel::plainJsonText(command["type"]) ||
      !OnlineLabel::plainJsonText(command["status"]) ||
      !OnlineLabel::plainJsonText(command["protocol"]) ||
      !OnlineLabel::plainJsonText(command["execute_before"]) ||
      !OnlineLabel::plainJsonText(command["exhibit_id"]) || !validIdentifier(id) ||
      strcmp(command["device_id"] | "", DEVICE_ID) ||
      strcmp(command["showcase_id"] | "", SHOWCASE_ID) ||
      strcmp(command["type"] | "", "assignment") || strcmp(command["status"] | "", "pending") ||
      strcmp(command["protocol"] | "", "device-fence-v1") ||
      !command["resource_generation"].is<uint32_t>() || !generation || generation > 999999999UL ||
      expectedLock != lock || (*journal.lock && strcmp(lock, journal.lock))) {
    Serial.println("ASSIGNMENT ERROR: invalid command identity, scope or fence.");
    return;
  }
  if (generation <= journal.generation) {
    Serial.println("ASSIGNMENT ERROR: stale command generation; not executed.");
    return;
  }
  time_t expiry = OnlineLabel::deadline(command["execute_before"] | "");
  if (expiry <= time(nullptr) + 30) {
    strlcpy(journal.lock, lock, sizeof(journal.lock));
    strlcpy(journal.command, id, sizeof(journal.command));
    journal.generation = generation;
    block("Command expired or too close to expiry; request recovery");
    return;
  }
  char serialProfile[201];
  if (!serialProfileForCommand(command, serialProfile, sizeof(serialProfile))) {
    Serial.println("ASSIGNMENT ERROR: invalid frozen monitoring profile.");
    return;
  }
  uint32_t boot = RfidBridge::megaBootCount();
  if (!boot) {
    Serial.println("ASSIGNMENT: waiting for Mega startup confirmation.");
    return;
  }
  strlcpy(journal.lock, lock, sizeof(journal.lock));
  strlcpy(journal.command, id, sizeof(journal.command));
  strlcpy(journal.exhibit, command["exhibit_id"] | "", sizeof(journal.exhibit));
  strlcpy(journal.profile, serialProfile, sizeof(journal.profile));
  journal.generation = generation;
  journal.phase = EXECUTING;
  verifiedBoot = 0;
  if (!saveJournal()) return;
  if (!RfidBridge::sendProfile(String(serialProfile), boot, expiry) ||
      RfidBridge::megaBootCount() != boot) {
    block("Mega result is unknown; do not submit a success receipt");
    return;
  }
  if (!verifyProfile()) {
    block("Mega could not verify the saved profile; no success receipt sent");
    return;
  }
  prepareAcknowledgement();
}

void tick() {
  if (!ONLINE_ASSIGNMENTS_ENABLED || !ready || journal.phase == BLOCKED ||
      (int32_t)(millis() - nextPoll) < 0) return;
  nextPoll = millis() + LABEL_POLL_MS;
  if (!RfidBridge::megaBootCount()) {
    MegaLink.println("PRO:1:H");
    Serial.println("ASSIGNMENT: waiting for Mega startup confirmation.");
    return;
  }
  if (journal.profile[0] && !verifyProfile()) {
    Serial.println("ASSIGNMENT: waiting to verify the Mega profile; no receipt sent.");
    return;
  }
  if (ONLINE_LABELS_ENABLED && (!OnlineLabel::ready || OnlineLabel::journal.phase != OnlineLabel::IDLE))
    return;
  JsonDocument document;
  if (journal.phase == ACK_PENDING) {
    int code = OnlineLabel::request(String("/api/commands/") + journal.command + "/acknowledgements",
                                    document, journal.acknowledgement);
    if (code == 200 || code == 201) {
      journal.phase = IDLE;
      journal.acknowledgement[0] = '\0';
      if (saveJournal()) Serial.println("ASSIGNMENT: cloud acknowledged the Mega profile.");
    } else if (code == 400 || code == 409 || code == 422) {
      block("Receipt rejected or command expired");
    } else {
      nextPoll = millis() + 60000UL;
    }
    return;
  }
  String path = String("/api/commands?device_id=") + DEVICE_ID + "&type=assignment&status=" +
                (pollingRecovery ? "recovery_pending" : "pending") + "&limit=1";
  if (cursor.length()) path += "&cursor=" + OnlineLabel::encodeQuery(cursor);
  int code = OnlineLabel::request(path, document);
  if (code != 200 || !document["items"].is<JsonArray>()) {
    if (code == 400) cursor = "";
    nextPoll = millis() + 60000UL;
    return;
  }
  if (!document["next_cursor"].isNull() &&
      !OnlineLabel::plainJsonText(document["next_cursor"])) {
    Serial.println("ASSIGNMENT ERROR: invalid continuation cursor.");
    nextPoll = millis() + 60000UL;
    return;
  }
  const char *next = document["next_cursor"] | "";
  if (strlen(next) > 2048) {
    Serial.println("ASSIGNMENT ERROR: cursor too large.");
    nextPoll = millis() + 60000UL;
    return;
  }
  cursor = next;
  JsonArrayConst items = document["items"];
  if (items.size() > 1) {
    Serial.println("ASSIGNMENT ERROR: unexpected command page size.");
    cursor = "";
    nextPoll = millis() + 60000UL;
    return;
  }
  if (pollingRecovery) {
    if (items.size()) {
      block("Recovery is pending; fence and reconcile the physical result");
    } else if (!cursor.length()) {
      pollingRecovery = false;
    }
    return;
  }
  if (!items.size()) {
    if (!cursor.length()) pollingRecovery = true;
    return;
  }
  const char *id = items[0]["command_id"] | "";
  if (!OnlineLabel::plainJsonText(items[0]["command_id"]) || !validIdentifier(id)) {
    Serial.println("ASSIGNMENT ERROR: invalid command ID in poll.");
    return;
  }
  String expectedId = id;
  String exact = String("/api/commands/") + expectedId;
  document.clear();
  if (OnlineLabel::request(exact, document) == 200) {
    JsonObjectConst item = document["item"];
    if (OnlineLabel::plainJsonText(item["command_id"]) &&
        expectedId == (item["command_id"] | "")) execute(item);
    else Serial.println("ASSIGNMENT ERROR: exact command ID mismatch.");
  }
  cursor = "";
  pollingRecovery = true;
}
}
