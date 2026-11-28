#pragma once

namespace OnlineAssignmentRecovery {
enum Phase : uint8_t { NONE, FENCED, RECEIPT_READY };
struct Journal {
  uint32_t magic;
  char device[65], showcase[65], command[65], exhibit[65], lock[201];
  char profile[201], recoveryId[65], acknowledgementId[65], statusId[65], recordedAt[28];
  uint32_t generation, fenceGeneration;
  time_t fencedAt;
  Phase phase;
  uint8_t outcome;
};
constexpr uint32_t MAGIC = 0x52415231;
Journal recovery = {};
bool ready = false;
uint32_t nextPoll = 0, verifiedBoot = 0;
uint8_t verifiedOutcome = 0;
String cursor;

bool save() {
  if (OnlineAssignment::preferences.putBytes("recovery-v1", &recovery, sizeof(recovery)) != sizeof(recovery)) {
    ready = false;
    OnlineAssignment::ready = false;
    Serial.println("RECOVERY ERROR: could not persist recovery journal; stopped.");
    return false;
  }
  return true;
}

void begin() {
  if (!ONLINE_ASSIGNMENTS_ENABLED || !OnlineAssignment::ready) return;
  size_t size = OnlineAssignment::preferences.getBytesLength("recovery-v1");
  if (size) {
    if (size != sizeof(recovery) ||
        OnlineAssignment::preferences.getBytes("recovery-v1", &recovery, sizeof(recovery)) != sizeof(recovery) ||
        recovery.magic != MAGIC || recovery.phase < FENCED || recovery.phase > RECEIPT_READY ||
        recovery.device[64] || recovery.showcase[64] || recovery.command[64] || recovery.exhibit[64] ||
        recovery.lock[200] || recovery.profile[200] || recovery.recoveryId[64] ||
        recovery.acknowledgementId[64] || recovery.statusId[64] || recovery.recordedAt[27] ||
        strcmp(recovery.device, DEVICE_ID) || strcmp(recovery.showcase, SHOWCASE_ID) ||
        strcmp(recovery.lock, OnlineAssignment::journal.lock) ||
        recovery.fenceGeneration != OnlineAssignment::journal.generation ||
        (OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED &&
         strcmp(recovery.command, OnlineAssignment::journal.command)) ||
        !validIdentifier(recovery.command) || !validIdentifier(recovery.exhibit) ||
        !validIdentifier(recovery.recoveryId) || !recovery.profile[0] ||
        !recovery.generation || recovery.fenceGeneration <= recovery.generation ||
        (recovery.phase == RECEIPT_READY &&
         (!validIdentifier(recovery.acknowledgementId) || !validIdentifier(recovery.statusId) ||
          !recovery.recordedAt[0] || (recovery.outcome != 1 && recovery.outcome != 2)))) {
      Serial.println("RECOVERY ERROR: invalid recovery journal; do not erase fences.");
      OnlineAssignment::ready = false;
      return;
    }
    strlcpy(OnlineAssignment::journal.command, recovery.command, sizeof(OnlineAssignment::journal.command));
    OnlineAssignment::journal.phase = OnlineAssignment::BLOCKED;
    if (!OnlineAssignment::saveJournal()) return;
  }
  ready = ONLINE_ASSIGNMENT_RECOVERY_ENABLED;
  if (!ready && size) Serial.println("RECOVERY: disabled; existing recovery remains fenced.");
}

bool bindCommand(JsonObjectConst item, const String &expectedId) {
  JsonObjectConst details = item["recovery"];
  JsonObjectConst request = details["request"];
  const char *id = item["command_id"] | "";
  const char *lock = item["lock_id"] | "";
  const char *recoveryId = request["recovery_id"] | "";
  uint32_t generation = item["resource_generation"] | 0U;
  uint32_t fence = details["fence_generation"] | 0U;
  String expectedLock = String("showcase#") + SHOWCASE_ID + "#assignment";
  if (!OnlineLabel::plainJsonText(item["command_id"]) || expectedId != id ||
      !OnlineLabel::plainJsonText(item["device_id"]) || strcmp(item["device_id"] | "", DEVICE_ID) ||
      !OnlineLabel::plainJsonText(item["showcase_id"]) || strcmp(item["showcase_id"] | "", SHOWCASE_ID) ||
      !OnlineLabel::plainJsonText(item["lock_id"]) || expectedLock != lock ||
      !OnlineLabel::plainJsonText(item["type"]) || strcmp(item["type"] | "", "assignment") ||
      !OnlineLabel::plainJsonText(item["status"]) || strcmp(item["status"] | "", "recovery_pending") ||
      !OnlineLabel::plainJsonText(item["protocol"]) || strcmp(item["protocol"] | "", "device-fence-v1") ||
      !OnlineLabel::plainJsonText(request["recovery_id"]) || !validIdentifier(recoveryId) ||
      !OnlineLabel::plainJsonText(details["requested_at"]) ||
      !item["resource_generation"].is<uint32_t>() || !generation ||
      !details["fence_generation"].is<uint32_t>() || fence <= generation || fence > 999999999UL ||
      !OnlineLabel::deadline(details["requested_at"] | "") ||
      OnlineLabel::deadline(details["requested_at"] | "") > time(nullptr) ||
      (*OnlineAssignment::journal.lock && strcmp(OnlineAssignment::journal.lock, lock)) ||
      (OnlineAssignment::journal.generation > generation &&
       (OnlineAssignment::journal.generation != fence || strcmp(OnlineAssignment::journal.command, id))) ||
      (OnlineAssignment::journal.generation == generation && *OnlineAssignment::journal.command &&
       strcmp(OnlineAssignment::journal.command, id))) {
    Serial.println("RECOVERY ERROR: invalid command, scope or fence.");
    return false;
  }
  Journal candidate = {};
  if (!OnlineAssignment::serialProfileForCommand(item, candidate.profile, sizeof(candidate.profile))) {
    Serial.println("RECOVERY ERROR: unsupported frozen profile; outcome remains unknown.");
    return false;
  }
  candidate.magic = MAGIC;
  strlcpy(candidate.device, DEVICE_ID, sizeof(candidate.device));
  strlcpy(candidate.showcase, SHOWCASE_ID, sizeof(candidate.showcase));
  strlcpy(candidate.command, id, sizeof(candidate.command));
  strlcpy(candidate.exhibit, item["exhibit_id"] | "", sizeof(candidate.exhibit));
  strlcpy(candidate.lock, lock, sizeof(candidate.lock));
  strlcpy(candidate.recoveryId, recoveryId, sizeof(candidate.recoveryId));
  candidate.generation = generation;
  candidate.fenceGeneration = fence;
  candidate.phase = FENCED;
  strlcpy(OnlineAssignment::journal.command, id, sizeof(OnlineAssignment::journal.command));
  strlcpy(OnlineAssignment::journal.lock, lock, sizeof(OnlineAssignment::journal.lock));
  OnlineAssignment::journal.generation = fence;
  OnlineAssignment::journal.phase = OnlineAssignment::BLOCKED;
  OnlineAssignment::verifiedBoot = 0;
  if (!OnlineAssignment::saveJournal()) return false;
  candidate.fencedAt = time(nullptr);
  recovery = candidate;
  if (!save()) return false;
  Serial.println("RECOVERY: command durably fenced; checking Mega.");
  return true;
}

bool verifyOutcome(uint8_t outcome) {
  uint32_t boot = RfidBridge::megaBootCount();
  if (!boot || (outcome != 1 && outcome != 2)) return false;
  bool verified = RfidBridge::sendProfile(String(recovery.profile), boot, time(nullptr) + 120,
                                           true, outcome == 2);
  if (!verified || RfidBridge::megaBootCount() != boot) return false;
  verifiedBoot = boot;
  verifiedOutcome = outcome;
  return true;
}

void statusUploaded(const Sample &sample) {
  if (!ready || recovery.phase != FENCED || !verifiedBoot ||
      verifiedBoot != RfidBridge::megaBootCount() || !verifiedOutcome) return;
  time_t observed = OnlineLabel::deadline(sample.timestamp);
  if (observed <= recovery.fencedAt || observed > time(nullptr) || time(nullptr) - observed > 90) return;
  char statusId[65];
  int length = snprintf(statusId, sizeof(statusId), "s-%s", sample.id);
  if (length <= 0 || (size_t)length >= sizeof(statusId) || !validIdentifier(statusId)) {
    Serial.println("RECOVERY ERROR: invalid status identity; waiting for another sample.");
    return;
  }
  snprintf(recovery.acknowledgementId, sizeof(recovery.acknowledgementId),
           "ra-%08lx%08lx%08lx", (unsigned long)esp_random(),
           (unsigned long)esp_random(), (unsigned long)esp_random());
  strlcpy(recovery.statusId, statusId, sizeof(recovery.statusId));
  strlcpy(recovery.recordedAt, sample.timestamp, sizeof(recovery.recordedAt));
  recovery.outcome = verifiedOutcome;
  recovery.phase = RECEIPT_READY;
  if (save()) Serial.println("RECOVERY: fresh device status accepted; preparing receipt.");
}

bool finalize() {
  OnlineAssignment::journal.phase = OnlineAssignment::IDLE;
  OnlineAssignment::journal.acknowledgement[0] = '\0';
  if (recovery.outcome == 1) {
    strlcpy(OnlineAssignment::journal.command, recovery.command, sizeof(OnlineAssignment::journal.command));
    strlcpy(OnlineAssignment::journal.exhibit, recovery.exhibit, sizeof(OnlineAssignment::journal.exhibit));
    strlcpy(OnlineAssignment::journal.profile, recovery.profile, sizeof(OnlineAssignment::journal.profile));
    OnlineAssignment::verifiedBoot = verifiedBoot;
  } else {
    OnlineAssignment::journal.command[0] = '\0';
    OnlineAssignment::journal.exhibit[0] = '\0';
    OnlineAssignment::journal.profile[0] = '\0';
    OnlineAssignment::verifiedBoot = 0;
  }
  if (!OnlineAssignment::saveJournal()) return false;
  if (!OnlineAssignment::preferences.remove("recovery-v1")) {
    ready = false;
    OnlineAssignment::ready = false;
    Serial.println("RECOVERY ERROR: could not clear completed recovery journal; stopped.");
    return false;
  }
  recovery = {};
  verifiedBoot = 0;
  verifiedOutcome = 0;
  Serial.println("RECOVERY: cloud confirmed the reconciled assignment.");
  return true;
}

void tick() {
  if (!ready || !OnlineAssignment::ready || OnlineAssignment::journal.phase != OnlineAssignment::BLOCKED ||
      (int32_t)(millis() - nextPoll) < 0) return;
  nextPoll = millis() + LABEL_POLL_MS;
  if (!RfidBridge::megaBootCount()) {
    MegaLink.println("PRO:1:H");
    return;
  }
  if (recovery.phase == NONE) {
    JsonDocument document;
    String path = String("/api/commands?device_id=") + DEVICE_ID + "&type=assignment&status=recovery_pending&limit=1";
    if (cursor.length()) path += "&cursor=" + OnlineLabel::encodeQuery(cursor);
    if (OnlineLabel::request(path, document) != 200 || !document["items"].is<JsonArray>()) return;
    if (!document["next_cursor"].isNull() && !OnlineLabel::plainJsonText(document["next_cursor"])) {
      Serial.println("RECOVERY ERROR: invalid cursor.");
      return;
    }
    const char *next = document["next_cursor"] | "";
    if (strlen(next) > 2048) {
      Serial.println("RECOVERY ERROR: cursor too large.");
      return;
    }
    cursor = next;
    JsonArrayConst items = document["items"];
    if (items.size() > 1) {
      Serial.println("RECOVERY ERROR: unexpected page size.");
      cursor = "";
      return;
    }
    if (!items.size()) return;
    const char *id = items[0]["command_id"] | "";
    if (!OnlineLabel::plainJsonText(items[0]["command_id"]) || !validIdentifier(id)) {
      Serial.println("RECOVERY ERROR: invalid command ID.");
      return;
    }
    String expectedId = id;
    document.clear();
    if (OnlineLabel::request(String("/api/commands/") + expectedId, document) == 200)
      bindCommand(document["item"], expectedId);
    cursor = "";
    return;
  }
  if (!verifiedBoot || verifiedBoot != RfidBridge::megaBootCount() ||
      (recovery.phase == RECEIPT_READY && verifiedOutcome != recovery.outcome)) {
    verifiedBoot = 0;
    verifiedOutcome = 0;
    if (recovery.phase == RECEIPT_READY) {
      if (!verifyOutcome(recovery.outcome)) {
        Serial.println("RECOVERY: saved outcome cannot be verified; lock remains held.");
        return;
      }
    } else if (!verifyOutcome(1) && !verifyOutcome(2)) {
      Serial.println("RECOVERY: Mega outcome is unknown; lock remains held.");
      return;
    }
  }
  if (recovery.phase != RECEIPT_READY) return;
  JsonDocument command;
  if (OnlineLabel::request(String("/api/commands/") + recovery.command, command) != 200) return;
  JsonObjectConst item = command["item"];
  JsonObjectConst details = item["recovery"];
  if (!OnlineLabel::plainJsonText(item["command_id"]) ||
      !OnlineLabel::plainJsonText(item["device_id"]) ||
      !OnlineLabel::plainJsonText(item["showcase_id"]) ||
      !OnlineLabel::plainJsonText(item["lock_id"]) ||
      !OnlineLabel::plainJsonText(item["status"]) ||
      !OnlineLabel::plainJsonText(details["request"]["recovery_id"]) ||
      !details["fence_generation"].is<uint32_t>() ||
      strcmp(item["command_id"] | "", recovery.command) ||
      strcmp(item["device_id"] | "", DEVICE_ID) ||
      strcmp(item["showcase_id"] | "", SHOWCASE_ID) ||
      strcmp(item["lock_id"] | "", recovery.lock) ||
      strcmp(details["request"]["recovery_id"] | "", recovery.recoveryId) ||
      (details["fence_generation"] | 0U) != recovery.fenceGeneration) {
    Serial.println("RECOVERY ERROR: cloud binding changed; lock remains held.");
    return;
  }
  if (!strcmp(item["status"] | "", "recovered")) {
    if (!strcmp(details["acknowledgement_id"] | "", recovery.acknowledgementId) &&
        !strcmp(details["outcome"] | "", recovery.outcome == 1 ? "applied" : "not_applied"))
      finalize();
    else Serial.println("RECOVERY ERROR: different cloud receipt; manual reconciliation required.");
    return;
  }
  if (strcmp(item["status"] | "", "recovery_pending")) {
    Serial.println("RECOVERY ERROR: cloud command is no longer pending recovery.");
    return;
  }
  time_t observed = OnlineLabel::deadline(recovery.recordedAt);
  if (!observed || observed > time(nullptr) || time(nullptr) - observed > 90) {
    recovery.phase = FENCED;
    recovery.acknowledgementId[0] = recovery.statusId[0] = recovery.recordedAt[0] = '\0';
    recovery.outcome = 0;
    recovery.fencedAt = time(nullptr);
    if (save()) Serial.println("RECOVERY: status is stale; waiting for a fresh observation.");
    return;
  }
  JsonDocument receipt;
  receipt["acknowledgement_id"] = recovery.acknowledgementId;
  receipt["recovery_id"] = recovery.recoveryId;
  receipt["device_id"] = DEVICE_ID;
  receipt["recorded_at"] = recovery.recordedAt;
  receipt["source"] = "device";
  receipt["fenced_generation"] = recovery.fenceGeneration;
  receipt["observed_status_id"] = recovery.statusId;
  receipt["outcome"] = recovery.outcome == 1 ? "applied" : "not_applied";
  char body[512];
  if (measureJson(receipt) >= sizeof(body)) {
    Serial.println("RECOVERY ERROR: receipt too large.");
    return;
  }
  serializeJson(receipt, body, sizeof(body));
  JsonDocument response;
  int code = OnlineLabel::request(String("/api/commands/") + recovery.command +
                                  "/recovery-acknowledgements", response, body);
  if (code == 200 || code == 201) finalize();
  else Serial.printf("RECOVERY: receipt HTTP %d; command remains blocked.\n", code);
}
}
