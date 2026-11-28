"""Run actual warning/layout firmware logic on the host; no network or boards."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from checks.firmware_test import function

ROOT = Path(__file__).resolve().parents[1]


class DisplayFirmwareTests(unittest.TestCase):
    def test_mega_rgb_indicators(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        sketch = (ROOT / "Final/Mega_Final/Mega_Final.ino").read_text(encoding="utf-8")
        source = r'''
#include <cassert>
#include <cstdint>
#include "Final/Mega_Final/WarningState.h"
const uint8_t LED_PINS[] = {40, 42, 38, 36, 34, 32};
const bool LED_ACTIVE_HIGH = true;
const int HIGH = 1, LOW = 0;
int pins[54] = {};
uint32_t now = 0;
uint32_t millis() { return now; }
void digitalWrite(uint8_t pin, int value) { pins[pin] = value; }
uint8_t lightingMode = 0;
bool occupancyKnown = false, occupied = false, lightsOn = false;
bool epaperCardFeedbackActive = false;
uint32_t epaperCardFeedbackStartedAt = 0;
const uint32_t epaperCardFeedbackDuration = 1000;
WarningState warningState;
uint16_t lightRaw = 400;
uint16_t warningMask = 0;
'''
        source += function(sketch, "void updateLighting() {")
        source += r'''
void expect(bool r1, bool g1, bool b1, bool r2, bool g2, bool b2) {
  const bool expected[] = {r1, g1, b1, r2, g2, b2};
  for (uint8_t i = 0; i < sizeof(LED_PINS); ++i)
    assert(pins[LED_PINS[i]] == expected[i]);
}
int main() {
  updateLighting();
  expect(false, false, false, false, false, false);
  occupancyKnown = true; occupied = true;
  lightRaw = 700;
  warningMask = warningState.update(2100, lightRaw, true, false, now);
  assert(!(warningMask & LIGHT_HIGH));
  updateLighting();
  expect(true, true, true, true, false, false);
  assert(lightsOn);
  now = 5000;
  warningMask = warningState.update(2100, lightRaw, true, false, now);
  assert(warningMask & LIGHT_HIGH);
  updateLighting();
  expect(true, true, true, true, false, false);
  epaperCardFeedbackActive = true; epaperCardFeedbackStartedAt = now;
  updateLighting();
  expect(false, true, false, true, false, false);
  assert(!lightsOn);
  now += 999; updateLighting();
  expect(false, true, false, true, false, false);
  ++now; updateLighting();
  expect(true, true, true, true, false, false);
  assert(lightsOn);
  lightRaw = 400;
  warningMask = warningState.update(2100, lightRaw, true, false, now);
  lightingMode = 2; updateLighting();
  expect(false, false, false, false, false, false);
  lightingMode = 1; occupied = false; updateLighting();
  expect(true, true, true, false, false, false);
  warningMask = TEMP_HIGH; updateLighting();
  expect(true, true, true, false, false, false);
  lightRaw = 100;
  warningMask = warningState.update(2100, lightRaw, true, false, now);
  assert(!(warningMask & LIGHT_LOW));
  lightingMode = 2; updateLighting();
  expect(false, false, false, true, true, false);
  epaperCardFeedbackActive = true; epaperCardFeedbackStartedAt = now;
  updateLighting();
  expect(false, true, false, true, true, false);
  assert(!lightsOn);
  now += 1000; updateLighting();
  expect(false, false, false, true, true, false);
  lightRaw = 300; updateLighting();
  expect(false, false, false, false, false, false);
  lightRaw = 600; updateLighting();
  expect(false, false, false, false, false, false);
  MonitoringLimits limits = {1800, 2400, 200, 800, 5};
  assert(warningState.applyProfile(limits));
  lightRaw = 700; updateLighting();
  expect(false, false, false, false, false, false);
  lightRaw = 801; updateLighting();
  expect(false, false, false, true, false, false);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "mega-rgb.cpp"
            binary = Path(temp) / "mega-rgb.exe"
            cpp.write_text(source, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_assignment_telemetry_preserves_acquired_exhibit(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        sketch = (ROOT / "Final/ESP32S3_Final/ESP32S3_Final.ino").read_text(encoding="utf-8")
        source = r'''
#include <cassert>
#include <cctype>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>
const char *DEVICE_ID = "device";
size_t strlcpy(char *out, const char *in, size_t capacity) {
  size_t n = strlen(in);
  if (capacity) { size_t c = n < capacity - 1 ? n : capacity - 1; memcpy(out, in, c); out[c] = 0; }
  return n;
}
'''
        source += function(sketch, "struct Sample {") + ";\n"
        source += "\n".join(function(sketch, signature) for signature in (
            "uint16_t crc16(const char *text) {", "bool validIdentifier(",
            "bool parseFrame(char *line, Sample &s) {",
            "bool makePayload(const Sample &s, bool status, char *out, size_t capacity) {",
        ))
        mega = (ROOT / "Final/Mega_Final/Mega_Final.ino").read_text(encoding="utf-8")
        source += r'''
#define F(value) value
namespace MegaFixture {
struct Port {
  std::ostringstream data;
  template<typename T> void print(T value) { data << value; }
  template<typename T> void println(T value) { data << value << "\r\n"; }
} Serial, Serial2;
uint32_t millis() { return 1234; }
uint32_t sequenceNumber = 0, eventCount = 2;
int16_t temperatureCenti = 2100, distanceMm = 123;
uint16_t lightRaw = 500;
uint8_t lightingMode = 0;
bool occupancyKnown = true, occupied = true, lightsOn = true;
struct Profile { char exhibit[65]; } activeProfile = {"ex-2"};
bool activeProfileValid = true, profileStorageHealthy = true;
'''
        source += function(mega, "void sendSample() {") + "\n}\n"
        source += r'''
bool parse(const std::string &body, Sample &sample) {
  char wire[256];
  snprintf(wire, sizeof(wire), "%s*%04X", body.c_str(), crc16(body.c_str()));
  return parseFrame(wire, sample);
}
int main() {
  Sample sample = {};
  const std::string fields = "10,1234,2100,500,123,1,2,0,1";
  assert(parse("V2," + fields + ",ex-1", sample));
  assert(sample.identityPresent && !strcmp(sample.exhibit, "ex-1"));
  strcpy(sample.id, "id-1"); strcpy(sample.timestamp, "2026-01-01T00:00:00Z");
  char output[768];
  assert(makePayload(sample, false, output, sizeof(output)));
  assert(strstr(output, "\"exhibit_id\":\"ex-1\""));
  assert(parse("V2," + fields + ",-", sample));
  assert(sample.identityPresent && !sample.exhibit[0]);
  assert(!makePayload(sample, false, output, sizeof(output)));
  assert(makePayload(sample, true, output, sizeof(output)));
  assert(parse("V1," + fields, sample) && !sample.identityPresent && !sample.exhibit[0]);
  assert(!parse("V2," + fields + ",bad,id", sample));
  MegaFixture::sendSample();
  std::string wire = MegaFixture::Serial2.data.str();
  assert(wire.size() > 2 && wire.substr(wire.size() - 2) == "\r\n");
  wire.resize(wire.size() - 2);
  char input[256];
  strcpy(input, wire.c_str());
  assert(parseFrame(input, sample) && sample.identityPresent && !strcmp(sample.exhibit, "ex-2"));
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "assignment-telemetry.cpp"
            binary = Path(temp) / "assignment-telemetry.exe"
            cpp.write_text(source, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_assignment_executor_confirmation_retry_and_restart(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        cli = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"
        if not compiler or not cli.is_file():
            self.skipTest("Host C++ compiler and installed ArduinoJson are required")
        libraries = subprocess.run(
            [str(cli), "lib", "list", "ArduinoJson", "--format", "json"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(libraries.returncode, 0, libraries.stderr)
        installed = json.loads(libraries.stdout).get("installed_libraries", [])
        if not installed:
            self.skipTest("ArduinoJson is not installed")
        library = Path(installed[0]["library"]["source_dir"])
        harness = r'''
#include <ArduinoJson.h>
#include <cassert>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>
using String = std::string;
#ifdef _WIN32
tm *gmtime_r(const time_t *value, tm *result) {
  return gmtime_s(result, value) == 0 ? result : nullptr;
}
#endif
const char *DEVICE_ID = "device", *SHOWCASE_ID = "showcase";
bool ONLINE_ASSIGNMENTS_ENABLED = true, ONLINE_ASSIGNMENT_RECOVERY_ENABLED = true;
bool ONLINE_LABELS_ENABLED = false, EPAPER_BENCH_TEST = false;
uint32_t LABEL_POLL_MS = 5000, fakeMillis = 100000;
uint32_t millis() { return fakeMillis; }
uint32_t esp_random() { static uint32_t v = 1; return v++; }
size_t strlcpy(char *out, const char *in, size_t capacity) {
  size_t n = strlen(in);
  if (capacity) { size_t c = n < capacity - 1 ? n : capacity - 1; memcpy(out, in, c); out[c] = 0; }
  return n;
}
struct SerialMock {
  template<typename... Args> void printf(const char *, Args...) {}
  void println(const char *) {}
} Serial, MegaLink;
std::vector<uint8_t> stored;
std::vector<uint8_t> recoveryStored;
bool failStore = false;
struct Preferences {
  bool begin(const char *, bool) { return true; }
  std::vector<uint8_t> &entry(const char *key) {
    return !strcmp(key, "recovery-v1") ? recoveryStored : stored;
  }
  size_t getBytesLength(const char *key) { return entry(key).size(); }
  size_t getBytes(const char *key, void *data, size_t size) {
    if (size != entry(key).size()) return 0;
    memcpy(data, entry(key).data(), size); return size;
  }
  size_t putBytes(const char *key, const void *data, size_t size) {
    if (failStore) return 0;
    const uint8_t *p = static_cast<const uint8_t *>(data);
    entry(key).assign(p, p + size); return size;
  }
  bool remove(const char *key) { entry(key).clear(); return true; }
};
struct Sample { char timestamp[28], id[64]; };
bool durableExecuting();
namespace RfidBridge {
uint32_t boot = 1;
bool applyResult = true, verifyResult = true, notAppliedResult = false;
int applications = 0, verifications = 0;
String sent;
uint32_t megaBootCount() { return boot; }
bool sendProfile(const String &data, uint32_t, time_t, bool verify = false, bool notApplied = false) {
  if (verify) { ++verifications; return notApplied ? notAppliedResult : verifyResult; }
  assert(durableExecuting());
  ++applications; sent = data; return applyResult;
}
}
namespace OnlineLabel {
enum Phase { IDLE, EXECUTING, ACK_PENDING, BLOCKED };
struct Journal { Phase phase = IDLE; } journal;
bool ready = true, recoveryPending = false;
int ackStatus = 201, ackCalls = 0;
time_t expiryOffset = 600;
String commandJson;
std::vector<String> ackBodies;
bool plainJsonText(JsonVariantConst value) {
  if (!value.is<const char *>()) return false;
  JsonString s = value.as<JsonString>(); return s.size() == strlen(s.c_str());
}
time_t deadline(const char *text) {
  if (!strcmp(text, "requested")) return time(nullptr) - 10;
  if (!strcmp(text, "sample")) return time(nullptr) - 1;
  return time(nullptr) + expiryOffset;
}
String encodeQuery(const String &s) { return s; }
int request(const String &path, JsonDocument &doc, const char *body = nullptr) {
  if (body) { ++ackCalls; ackBodies.emplace_back(body); return ackStatus; }
  if (path.find("recovery_pending") != String::npos)
    return deserializeJson(doc, recoveryPending
      ? "{\"items\":[{\"command_id\":\"assign-1\"}],\"next_cursor\":null}"
      : "{\"items\":[],\"next_cursor\":null}") ? 500 : 200;
  if (path.find("status=pending") != String::npos)
    return deserializeJson(doc, "{\"items\":[{\"command_id\":\"assign-1\"}],\"next_cursor\":null}") ? 500 : 200;
  return deserializeJson(doc, commandJson) ? 500 : 200;
}
}
'''
        sketch = (ROOT / "Final/ESP32S3_Final/ESP32S3_Final.ino").read_text(encoding="utf-8")
        harness += function(sketch, "bool validIdentifier(")
        harness += r'''
#include "Final/ESP32S3_Final/OnlineAssignment.h"
#include "Final/ESP32S3_Final/OnlineAssignmentRecovery.h"
bool durableExecuting() {
  if (stored.size() != sizeof(OnlineAssignment::Journal)) return false;
  OnlineAssignment::Journal saved;
  memcpy(&saved, stored.data(), sizeof(saved));
  return saved.phase == OnlineAssignment::EXECUTING && saved.generation == 1;
}
void advance() { fakeMillis += 60001; OnlineAssignment::tick(); }
int main() {
  OnlineLabel::commandJson = R"json({"item":{"command_id":"assign-1","device_id":"device",
    "showcase_id":"showcase","exhibit_id":"ex-1","type":"assignment","status":"pending",
    "protocol":"device-fence-v1","resource_generation":1,"lock_id":"showcase#showcase#assignment",
    "execute_before":"future","payload":{"exhibit_id":"ex-1","profile":{"exhibit_id":"ex-1",
    "version":2,"temperature_min_c":18,"temperature_max_c":24,
    "brightness_min_percent":30,"brightness_max_percent":60,"warning_seconds":5}}}})json";
  OnlineAssignment::begin();
  advance(); // recovery sweep
  advance(); // exact-read and apply
  assert(RfidBridge::applications == 1);
  assert(RfidBridge::sent == "1,2,ex-1,assign-1,1800,2400,307,613,5");
  assert(OnlineAssignment::journal.phase == OnlineAssignment::ACK_PENDING);
  assert(OnlineLabel::ackCalls == 0 && !OnlineAssignment::labelReady());
  OnlineLabel::ackStatus = 503;
  advance();
  assert(OnlineLabel::ackCalls == 1 && OnlineAssignment::journal.phase == OnlineAssignment::ACK_PENDING);
  OnlineLabel::ackStatus = 201;
  advance();
  assert(OnlineLabel::ackCalls == 2 && OnlineLabel::ackBodies[0] == OnlineLabel::ackBodies[1]);
  assert(OnlineAssignment::labelBindingMatches("ex-1", "assign-1"));
  assert(!OnlineAssignment::labelBindingMatches("ex-1", "other"));
  // After restart, the Mega must confirm the complete saved profile before use.
  OnlineAssignment::verifiedBoot = 0;
  OnlineAssignment::ready = false;
  OnlineAssignment::begin();
  RfidBridge::verifyResult = false;
  advance();
  assert(!OnlineAssignment::labelReady());
  RfidBridge::verifyResult = true;
  advance();
  assert(OnlineAssignment::labelReady());
  // Any reset during the physical operation blocks rather than faking success.
  OnlineAssignment::journal.phase = OnlineAssignment::EXECUTING;
  assert(OnlineAssignment::saveJournal());
  OnlineAssignment::ready = false;
  OnlineAssignment::begin();
  assert(OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED);
  assert(!OnlineAssignment::labelReady());

  auto reset = []() {
    stored.clear();
    failStore = false;
    OnlineAssignment::journal = {};
    OnlineAssignment::ready = false;
    OnlineAssignment::verifiedBoot = 0;
    OnlineAssignment::nextPoll = 0;
    OnlineAssignment::pollingRecovery = true;
    OnlineAssignment::cursor.clear();
    RfidBridge::applications = RfidBridge::verifications = 0;
    RfidBridge::applyResult = RfidBridge::verifyResult = true;
    OnlineLabel::recoveryPending = false;
    OnlineLabel::ackCalls = 0;
    OnlineLabel::ackStatus = 201;
    OnlineLabel::ackBodies.clear();
    OnlineLabel::expiryOffset = 600;
    OnlineAssignment::begin();
  };
  reset();
  OnlineLabel::recoveryPending = true;
  advance();
  assert(OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED);
  assert(RfidBridge::applications == 0 && OnlineLabel::ackCalls == 0);

  reset();
  OnlineLabel::expiryOffset = 30;
  advance(); advance();
  assert(OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED);
  assert(RfidBridge::applications == 0 && OnlineLabel::ackCalls == 0);

  reset();
  RfidBridge::applyResult = false;
  advance(); advance();
  assert(RfidBridge::applications == 1);
  assert(OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED);
  assert(OnlineLabel::ackCalls == 0);

  reset();
  RfidBridge::verifyResult = false;
  advance(); advance();
  assert(RfidBridge::applications == 1 && RfidBridge::verifications == 1);
  assert(OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED);
  assert(OnlineLabel::ackCalls == 0);

  reset();
  failStore = true;
  advance(); advance();
  assert(!OnlineAssignment::ready && RfidBridge::applications == 0);
  assert(OnlineLabel::ackCalls == 0);

  reset();
  advance(); advance();
  assert(OnlineAssignment::journal.phase == OnlineAssignment::ACK_PENDING);
  String receipt = OnlineAssignment::journal.acknowledgement;
  OnlineAssignment::ready = false;
  OnlineAssignment::verifiedBoot = 0;
  OnlineAssignment::begin();
  RfidBridge::verifyResult = false;
  advance();
  assert(OnlineLabel::ackCalls == 0);
  RfidBridge::verifyResult = true;
  advance();
  assert(OnlineLabel::ackCalls == 1 && OnlineLabel::ackBodies[0] == receipt);

  reset();
  String original = OnlineLabel::commandJson;
  size_t idAt = OnlineLabel::commandJson.find("\"command_id\":\"assign-1\"");
  assert(idAt != String::npos);
  OnlineLabel::commandJson.replace(idAt, strlen("\"command_id\":\"assign-1\""),
                                   "\"command_id\":\"assign-2\"");
  advance(); advance();
  assert(RfidBridge::applications == 0 && OnlineLabel::ackCalls == 0);
  OnlineLabel::commandJson = original;

  reset();
  String invalid = original;
  size_t deviceAt = invalid.find("\"device_id\":\"device\"");
  assert(deviceAt != String::npos);
  invalid.replace(deviceAt, strlen("\"device_id\":\"device\""),
                  "\"device_id\":\"device\\u0000extra\"");
  JsonDocument invalidDocument;
  assert(!deserializeJson(invalidDocument, invalid));
  OnlineAssignment::execute(invalidDocument["item"]);
  assert(RfidBridge::applications == 0 && OnlineLabel::ackCalls == 0);

  reset();
  recoveryStored.clear();
  OnlineAssignment::journal.phase = OnlineAssignment::BLOCKED;
  strcpy(OnlineAssignment::journal.command, "assign-1");
  OnlineAssignment::journal.generation = 1;
  assert(OnlineAssignment::saveJournal());
  OnlineAssignmentRecovery::recovery = {};
  OnlineAssignmentRecovery::ready = false;
  OnlineAssignmentRecovery::nextPoll = OnlineAssignmentRecovery::verifiedBoot = 0;
  OnlineAssignmentRecovery::verifiedOutcome = 0;
  OnlineAssignmentRecovery::cursor.clear();
  OnlineLabel::recoveryPending = true;
  OnlineLabel::commandJson = R"json({"item":{"command_id":"assign-1","device_id":"device",
    "showcase_id":"showcase","exhibit_id":"ex-1","type":"assignment","status":"recovery_pending",
    "protocol":"device-fence-v1","resource_generation":1,"lock_id":"showcase#showcase#assignment",
    "recovery":{"request":{"recovery_id":"recover-1"},"fence_generation":2,"requested_at":"requested"},
    "payload":{"exhibit_id":"ex-1","profile":{"exhibit_id":"ex-1","version":2,
    "temperature_min_c":18,"temperature_max_c":24,"brightness_min_percent":30,
    "brightness_max_percent":60,"warning_seconds":5}}}})json";
  OnlineAssignmentRecovery::begin();
  fakeMillis += 60001; OnlineAssignmentRecovery::tick();
  assert(OnlineAssignmentRecovery::recovery.phase == OnlineAssignmentRecovery::FENCED);
  assert(OnlineAssignment::journal.generation == 2 && !recoveryStored.empty());
  OnlineAssignmentRecovery::ready = false;
  OnlineAssignmentRecovery::recovery = {};
  OnlineAssignmentRecovery::begin();
  assert(OnlineAssignmentRecovery::recovery.phase == OnlineAssignmentRecovery::FENCED);
  assert(OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED);
  RfidBridge::verifyResult = false;
  RfidBridge::notAppliedResult = false;
  fakeMillis += 60001; OnlineAssignmentRecovery::tick();
  assert(OnlineAssignmentRecovery::verifiedOutcome == 0 && OnlineLabel::ackCalls == 0);
  RfidBridge::notAppliedResult = true;
  fakeMillis += 60001; OnlineAssignmentRecovery::tick();
  assert(OnlineAssignmentRecovery::verifiedOutcome == 2);
  OnlineAssignmentRecovery::recovery.fencedAt = time(nullptr) - 5;
  Sample sample = {};
  strcpy(sample.id, "sample-1");
  strcpy(sample.timestamp, "sample");
  OnlineAssignmentRecovery::statusUploaded(sample);
  assert(OnlineAssignmentRecovery::recovery.phase == OnlineAssignmentRecovery::RECEIPT_READY);
  OnlineLabel::ackStatus = 503;
  fakeMillis += 60001; OnlineAssignmentRecovery::tick();
  assert(OnlineLabel::ackCalls == 1 && OnlineAssignment::journal.phase == OnlineAssignment::BLOCKED);
  OnlineLabel::ackStatus = 201;
  fakeMillis += 60001; OnlineAssignmentRecovery::tick();
  assert(OnlineLabel::ackCalls == 2 && OnlineLabel::ackBodies[0] == OnlineLabel::ackBodies[1]);
  assert(OnlineAssignment::journal.phase == OnlineAssignment::IDLE);
  assert(recoveryStored.empty() && OnlineAssignment::journal.generation == 2);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            shutil.copytree(library, temp_path / "ArduinoJson")
            cpp = temp_path / "assignment-executor.cpp"
            binary = temp_path / "assignment-executor.exe"
            cpp.write_text(harness, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-static",
                 "-I", str(temp_path / "ArduinoJson"), "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_assignment_profile_conversion_and_invalid_limits(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        source = r'''
#include <cassert>
#include <limits>
#include "Final/ESP32S3_Final/AssignmentProfile.h"
int main() {
  AssignmentLimits result = {};
  assert(assignmentLimits(18, 24, 30, 60, 5, result));
  assert(result.temperatureMin == 1800 && result.temperatureMax == 2400);
  assert(result.lightMin == 307 && result.lightMax == 613 && result.warningSeconds == 5);
  assert(assignmentLimits(0, 125, 0, 100, 3600, result));
  assert(result.lightMin == 0 && result.lightMax == 1023);
  assert(assignmentLimits(18.01, 24.99, 0, 100, 1, result));
  assert(result.temperatureMin == 1801 && result.temperatureMax == 2499);
  for (int centi = 1; centi <= 12500; ++centi) {
    assert(assignmentLimits(0, centi / 100.0, 0, 100, 1, result));
    assert(result.temperatureMax == centi);
  }
  assert(!assignmentLimits(18.001, 24, 30, 60, 5, result));
  assert(!assignmentLimits(18.00000000001, 24, 30, 60, 5, result));
  assert(!assignmentLimits(18, 24, 0.1, 0.19, 5, result));
  assert(!assignmentLimits(18, 24, 100.0 / 1023, 60, 5, result));
  assert(!assignmentLimits(18, 24, 0, 100, 0, result));
  assert(!assignmentLimits(18, 24, 0, 100, 3601, result));
  assert(!assignmentLimits(-1, 24, 0, 100, 5, result));
  assert(!assignmentLimits(18, 24, 0, std::numeric_limits<double>::infinity(), 5, result));
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "assignment-profile.cpp"
            binary = Path(temp) / "assignment-profile.exe"
            cpp.write_text(source, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_final_esp_profile_transfer_requires_correlated_mega_reply(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        bridge = (ROOT / "Final/ESP32S3_Final/RfidBridge.h").read_text(encoding="utf-8")
        sketch = (ROOT / "Final/ESP32S3_Final/ESP32S3_Final.ino").read_text(encoding="utf-8")
        code = function(sketch, "uint16_t crc16(const char *text) {") + "\n"
        code += "\n".join(function(bridge, name) for name in ("bool sendPart(", "bool sendProfile("))
        harness = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>
struct String : std::string {
  using std::string::string;
  String(const std::string &value) : std::string(value) {}
  String(uint16_t value) : std::string(std::to_string(value)) {}
  String(uint32_t value) : std::string(std::to_string(value)) {}
  String(size_t value) : std::string(std::to_string(value)) {}
  String substring(size_t start, size_t end) const { return substr(start, end - start); }
};
struct Reply { char text[32]; };
struct SerialMock {
  template<typename... Args> void printf(const char *, Args...) {}
  void println(const char *) {}
} Serial;
struct LinkMock {
  std::string current;
  std::vector<std::string> lines;
  void print(const String &value) { current += value; }
  void println() { lines.push_back(current); current.clear(); }
} MegaLink;
std::atomic<uint32_t> megaBoots{1};
void *replies = &MegaLink;
uint16_t transfer = 0;
uint32_t profileTransfer = 0;
uint32_t clockMs = 0;
uint32_t millis() { return ++clockMs; }
int pdMS_TO_TICKS(int value) { return value; }
bool failEnd = false, resetOnBegin = false, corruptReply = false;
uint16_t crc16(const char *text);
int xQueueReceive(void *, Reply *reply, int) {
  const std::string &line = MegaLink.lines.back();
  if (resetOnBegin) { ++megaBoots; return 0; }
  size_t end = line.find(':', line.find(':', 4) + 1);
  std::string suffix = line.substr(4, end - 4);
  bool final = suffix.size() >= 2 && suffix.substr(suffix.size() - 2) == ":E";
  std::string response = (failEnd && final ? "PRO_ERR:" : "PRO_ACK:") + suffix;
  char checksum[6];
  snprintf(checksum, sizeof(checksum), "*%04X", crc16(response.c_str()));
  response += checksum;
  if (corruptReply) response.back() = response.back() == '0' ? '1' : '0';
  assert(response.size() < sizeof(reply->text));
  strcpy(reply->text, response.c_str());
  return 1;
}
'''
        assertions = r'''
int main() {
  String data("1,2,ex-1,cmd-1,1800,2400,200,800,5");
  assert(sendProfile(data, 1, time(nullptr) + 600));
  assert(MegaLink.lines.size() == 4);
  assert(MegaLink.lines[0] == "PRO:1:B:34");
  assert(MegaLink.lines[1] == "PRO:1:0:1,2,ex-1,cmd-1,1800,2400");
  assert(MegaLink.lines[2] == "PRO:1:1:,200,800,5");
  char checksum[5];
  snprintf(checksum, sizeof(checksum), "%04X", crc16(data.c_str()));
  assert(MegaLink.lines[3] == std::string("PRO:1:E:") + checksum);
  MegaLink.lines.clear();
  assert(sendProfile(data, 1, time(nullptr) + 600, true));
  assert(MegaLink.lines.back().find(":Q:") != std::string::npos);
  MegaLink.lines.clear();
  assert(sendProfile(data, 1, time(nullptr) + 600, true, true));
  assert(MegaLink.lines.back().find(":N:") != std::string::npos);
  MegaLink.lines.clear();
  assert(!sendProfile(data, 1, time(nullptr) + 30));
  assert(MegaLink.lines.empty());
  corruptReply = true;
  assert(!sendProfile(data, 1, time(nullptr) + 600));
  assert(MegaLink.lines.size() == 1);
  MegaLink.lines.clear();
  corruptReply = false;
  failEnd = true;
  assert(!sendProfile(data, 1, time(nullptr) + 600));
  MegaLink.lines.clear();
  failEnd = false;
  resetOnBegin = true;
  assert(!sendProfile(data, 1, time(nullptr) + 600));
  assert(MegaLink.lines.size() == 1);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "esp-profile-uart.cpp"
            binary = Path(temp) / "esp-profile-uart.exe"
            cpp.write_text(harness + code + assertions, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_final_mega_profile_transfer_requires_complete_valid_payload(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        sketch = (ROOT / "Final/Mega_Final/Mega_Final.ino").read_text(encoding="utf-8")
        panel = (ROOT / "Final/Mega_Final/MegaPanel.h").read_text(encoding="utf-8")
        code = "\n".join(function(sketch, name) for name in (
            "bool profileNumber(", "bool receiveCloudProfile(const char *text, bool verifyOnly, bool verifyNotApplied) {", "uint16_t crc16(const char *text) {",
        )) + "\n" + function(panel, "void checkEpaperResponse(")
        fake_eeprom = r'''
#pragma once
#include <cstdint>
struct FakeEEPROM {
  uint8_t bytes[4096];
  uint8_t read(int address) { return bytes[address]; }
  void update(int address, uint8_t value) { bytes[address] = value; }
};
extern FakeEEPROM EEPROM;
'''
        harness = r'''
#include <cassert>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "Final/Mega_Final/ProfileStore.h"
#define F(x) x
FakeEEPROM EEPROM;
WarningState warningState;
bool profileStorageHealthy = true;
ProfileStore::Profile activeProfile = {};
bool activeProfileValid = false;
int16_t dayMinTemp = 32767, dayMaxTemp = -32767;
uint32_t dayStarted = 0;
uint32_t millis() { return 123; }
struct MockSerial {
  std::string input, output;
  size_t cursor = 0;
  int available() { return cursor < input.size(); }
  char read() { return input[cursor++]; }
  void print(const char *text) { output += text; }
  void print(unsigned long value) { output += std::to_string(value); }
  void print(char value) { output += value; }
  void println(const char *text) { output += text; output += '\n'; }
} Serial, Serial2;
bool drawMegaPanel(const char *) { return true; }
'''
        assertions = r'''
void send(const std::string &text) {
  Serial2.input = text;
  Serial2.cursor = 0;
  Serial2.output.clear();
  checkEpaperResponse();
}
bool replyEquals(const std::string &base) {
  char checksum[6];
  snprintf(checksum, sizeof(checksum), "*%04X", crc16(base.c_str()));
  return Serial2.output == base + checksum + "\n";
}
void transfer(const std::string &payload, bool goodCrc, bool verifyOnly = false, bool verifyNotApplied = false) {
  send("PRO:17:B:" + std::to_string(payload.size()) + "\n");
  assert(replyEquals("PRO_ACK:17:B"));
  for (size_t start = 0, index = 0; start < payload.size(); start += 24, ++index) {
    send("PRO:17:" + std::to_string(index) + ":" + payload.substr(start, 24) + "\n");
    assert(replyEquals("PRO_ACK:17:" + std::to_string(index)));
  }
  char checksum[5];
  snprintf(checksum, sizeof(checksum), "%04X", crc16(payload.c_str()) ^ (goodCrc ? 0 : 1));
  send(std::string(verifyNotApplied ? "PRO:17:N:" : (verifyOnly ? "PRO:17:Q:" : "PRO:17:E:")) + checksum + "\n");
}
int main() {
  memset(EEPROM.bytes, 0xFF, sizeof(EEPROM.bytes));
  const std::string profile = "1,2,ex-1,cmd-1,1800,2400,200,800,5";
  send("PRO:17:H\n");
  assert(Serial2.output.find("EPD_READY\n") == 0);
  transfer(profile, true, true, true);
  assert(replyEquals("PRO_ACK:17:N"));
  transfer(profile, false);
  assert(replyEquals("PRO_ERR:17:E"));
  assert(!warningState.profileActive);
  transfer(profile, true);
  assert(replyEquals("PRO_ACK:17:E"));
  assert(warningState.profileActive && warningState.limits.lightMin == 200);
  transfer(profile, true, true, true);
  assert(replyEquals("PRO_ERR:17:N"));
  transfer("2,2,ex-1,cmd-2,1800,2400,200,800,5", true, true, true);
  assert(replyEquals("PRO_ACK:17:N"));
  assert(warningState.limits.lightMin == 200);
  dayMinTemp = 1900;
  transfer(profile, true, true);
  assert(replyEquals("PRO_ACK:17:Q"));
  assert(dayMinTemp == 1900);
  transfer("2,2,ex-1,cmd-2,1800,2400,200,800,5", true, true);
  assert(replyEquals("PRO_ERR:17:Q"));
  assert(warningState.limits.lightMin == 200);
  transfer(profile, true);
  assert(replyEquals("PRO_ACK:17:E"));
  assert(dayMinTemp == 1900);
  transfer("1,2,ex-1,cmd-1,1800,2400,201,800,5", true);
  assert(replyEquals("PRO_ERR:17:E"));
  assert(warningState.limits.lightMin == 200);
  transfer("2,2,ex-1,cmd-2,1800,2400,200,800,0", true);
  assert(replyEquals("PRO_ERR:17:E"));
  ProfileStore::Profile saved = {};
  uint8_t slot = 0;
  assert(ProfileStore::load(saved, slot) == ProfileStore::VALID);
  assert(saved.generation == 1);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            (temp_path / "EEPROM.h").write_text(fake_eeprom, encoding="utf-8")
            cpp = temp_path / "mega-profile-uart.cpp"
            binary = temp_path / "mega-profile-uart.exe"
            cpp.write_text(harness + code + assertions, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(temp_path), "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_final_mega_profile_store_power_loss(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        fake_eeprom = r'''
#pragma once
#include <cstdint>
struct FakeEEPROM {
  uint8_t bytes[4096];
  int writes = 0, failAfter = -1;
  uint8_t read(int address) { return bytes[address]; }
  void update(int address, uint8_t value) {
    if (failAfter < 0 || writes < failAfter) bytes[address] = value;
    ++writes;
  }
};
extern FakeEEPROM EEPROM;
'''
        source = r'''
#include <cassert>
#include <cstring>
#include "Final/Mega_Final/ProfileStore.h"
FakeEEPROM EEPROM;
using namespace ProfileStore;
int main() {
  memset(EEPROM.bytes, 0xFF, sizeof(EEPROM.bytes));
  Profile oldProfile = {};
  oldProfile.generation = 1;
  oldProfile.version = 2;
  strcpy(oldProfile.exhibit, "ex-1");
  strcpy(oldProfile.command, "cmd-1");
  oldProfile.limits = {1800, 2400, 200, 800, 5};
  assert(save(oldProfile));
  uint8_t initial[4096];
  memcpy(initial, EEPROM.bytes, sizeof(initial));
  Profile next = oldProfile;
  next.generation = 2;
  next.version = 3;
  strcpy(next.command, "cmd-2");
  next.limits.warningSeconds = 10;
  for (int stop = 0; stop <= (int)sizeof(Profile) + 2; ++stop) {
    memcpy(EEPROM.bytes, initial, sizeof(initial));
    EEPROM.writes = 0;
    EEPROM.failAfter = stop;
    bool saved = save(next);
    Profile restored = {};
    uint8_t slot = 0;
    assert(load(restored, slot) == VALID);
    assert(same(restored, saved ? next : oldProfile));
    assert(!memcmp(EEPROM.bytes, initial, SLOT_BASE));
  }
  EEPROM.failAfter = -1;
  memcpy(EEPROM.bytes, initial, sizeof(initial));
  assert(save(next));
  int writes = EEPROM.writes;
  assert(save(next));
  assert(EEPROM.writes == writes);
  assert(!save(oldProfile));
  Profile conflict = next;
  conflict.limits.lightMin++;
  assert(!save(conflict));
  EEPROM.bytes[SLOT_BASE + SLOT_SIZE + 1] ^= 1;
  Profile restored = {};
  uint8_t slot = 0;
  assert(load(restored, slot) == CORRUPT);
  assert(!save(next));
}
'''
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            (temp_path / "EEPROM.h").write_text(fake_eeprom, encoding="utf-8")
            cpp = temp_path / "mega-profile.cpp"
            binary = temp_path / "mega-profile.exe"
            cpp.write_text(source, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(temp_path), "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_final_mega_monitoring_limits(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        source = r'''
#include <cassert>
#include "Final/Mega_Final/WarningState.h"
int main() {
  WarningState state;
  assert(state.update(2200, 400, true, true, 0) == TEMP_SWING);
  assert(state.update(2200, 400, true, false, 5000) == TEMP_CAUTION);
  const MonitoringLimits profile = {1500, 2500, 205, 818, 2};
  assert(state.applyProfile(profile));
  assert(state.update(2500, 819, true, true, 5001) == 0);
  assert(state.update(2500, 819, true, true, 7000) == 0);
  assert(state.update(2500, 819, true, true, 7001) == (TEMP_HIGH | LIGHT_HIGH));
  assert(state.update(1500, 205, true, true, 7002) == 0);
  state.update(1499, 204, true, false, 7003);
  assert(state.update(1499, 204, true, false, 9003) == (TEMP_LOW | LIGHT_LOW));
  assert(state.update(-32768, 205, true, false, 9004) == TEMP_FAULT);
  assert(state.update(1499, 205, true, false, 9005) == 0);
  assert(state.update(1499, 205, true, false, 11005) == TEMP_LOW);
  MonitoringLimits invalid = profile;
  invalid.warningSeconds = 0;
  assert(!state.applyProfile(invalid));
  assert(state.update(1499, 205, true, false, 11006) == TEMP_LOW);
  invalid = profile;
  invalid.lightMin = 819;
  assert(!state.applyProfile(invalid));
  assert(state.applyProfile(profile));
  assert(state.update(1499, 205, true, false, 11007) == 0);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "mega-monitoring.cpp"
            binary = Path(temp) / "mega-monitoring.exe"
            cpp.write_text(source, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_final_mega_uart_transfer_and_failure(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        bridge = (ROOT / "Final/ESP32S3_Final/RfidBridge.h").read_text(encoding="utf-8")
        sketch = (ROOT / "Final/ESP32S3_Final/ESP32S3_Final.ino").read_text(encoding="utf-8")
        helpers = function(sketch, "uint16_t crc16(const char *text) {") + "\n"
        helpers += "\n".join(function(bridge, name) for name in (
            "bool sendPart(", "bool draw(",
        ))
        harness = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "Final/ESP32S3_Final/LabelLayout.h"
struct String : std::string {
  using std::string::string;
  String(const std::string &value) : std::string(value) {}
  String(uint16_t value) : std::string(std::to_string(value)) {}
  String(uint32_t value) : std::string(std::to_string(value)) {}
  String(size_t value) : std::string(std::to_string(value)) {}
  String substring(size_t start, size_t end) const { return substr(start, end - start); }
};
struct Reply { char text[32]; };
struct SerialMock {
  template<typename... Args> void printf(const char *, Args...) {}
} Serial;
struct LinkMock {
  std::string current;
  std::vector<std::string> lines;
  void print(const String &value) { current += value; }
  void println() { lines.push_back(current); current.clear(); }
} MegaLink;
std::atomic<uint32_t> megaBoots{1};
void *replies = &MegaLink;
uint16_t transfer = 0;
uint32_t clockMs = 0;
uint32_t millis() { return ++clockMs; }
int pdMS_TO_TICKS(int value) { return value; }
bool failEnd = false, resetOnBegin = false;
int xQueueReceive(void *, Reply *reply, int) {
  const std::string &line = MegaLink.lines.back();
  if (resetOnBegin) { ++megaBoots; return 0; }
  const bool end = line.substr(line.size() - 2) == ":E";
  std::string response = (failEnd && end ? "EPD_ERR:" : "EPD_ACK:") + line.substr(4);
  size_t endOfCommand = response.find(':', 8);
  if (endOfCommand != std::string::npos) {
    endOfCommand = response.find(':', endOfCommand + 1);
    if (endOfCommand != std::string::npos) response.resize(endOfCommand);
  }
  assert(response.size() < sizeof(reply->text));
  strcpy(reply->text, response.c_str());
  return 1;
}
bool RFID_PANEL_ON_MEGA = true;
bool useEspPanel() { return false; }
namespace OnlineLabel { bool render(const LabelLayout &) { return false; } }
'''
        assertions = r'''
int main() {
  String text("1234567890123456789012345:more");
  assert(draw(text, false, 1));
  assert(MegaLink.lines.size() == 4);
  assert(MegaLink.lines[0] == "EPD:1:B:30");
  assert(MegaLink.lines[1] == "EPD:1:0:123456789012345678901234");
  assert(MegaLink.lines[2] == "EPD:1:1:5:more");
  assert(MegaLink.lines[3] == "EPD:1:E");
  MegaLink.lines.clear();
  failEnd = true;
  assert(!draw(String("short"), false, 1));
  assert(MegaLink.lines.back() == "EPD:2:E");
  MegaLink.lines.clear();
  failEnd = false;
  resetOnBegin = true;
  assert(!draw(String("short"), false, 1));
  assert(MegaLink.lines.size() == 1);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "mega-cloud-uart.cpp"
            binary = Path(temp) / "mega-cloud-uart.exe"
            cpp.write_text(harness + helpers + assertions, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_final_cloud_label_requires_mega_confirmation(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        online = (ROOT / "Final/ESP32S3_Final/OnlineLabel.h").read_text(encoding="utf-8")
        execute = function(online, "void execute(JsonObjectConst command)")
        harness = r'''
#include <cassert>
#include <cstring>
#include <ctime>
#include <string>
#include "Final/ESP32S3_Final/LabelLayout.h"
using String = std::string;
const char *DEVICE_ID = "device", *SHOWCASE_ID = "showcase", *EXHIBIT_ID = "exhibit";
const char *description = "Description";
bool ONLINE_ASSIGNMENTS_ENABLED = false, assignmentMatches = true;
namespace OnlineAssignment {
bool labelBindingMatches(const char *, const char *) { return assignmentMatches; }
}
struct JsonObjectConst;
struct Field {
  const char *name;
  const char *text() const {
    if (!strcmp(name, "command_id")) return "command";
    if (!strcmp(name, "lock_id")) return "lock";
    if (!strcmp(name, "device_id")) return DEVICE_ID;
    if (!strcmp(name, "showcase_id")) return SHOWCASE_ID;
    if (!strcmp(name, "type")) return "label";
    if (!strcmp(name, "status")) return "pending";
    if (!strcmp(name, "protocol")) return "device-fence-v1";
    if (!strcmp(name, "execute_before")) return "future";
    if (!strcmp(name, "exhibit_id")) return EXHIBIT_ID;
    if (!strcmp(name, "assignment_command_id")) return "assignment";
    if (!strcmp(name, "title")) return "Title";
    if (!strcmp(name, "collection")) return "Collection";
    if (!strcmp(name, "description")) return description;
    return "";
  }
  const char *operator|(const char *) const { return text(); }
  uint32_t operator|(uint32_t) const { return 1; }
  operator const char *() const { return text(); }
  operator JsonObjectConst() const;
  template<typename T> bool is() const { return true; }
  template<typename T> T as() const { return static_cast<T>(1); }
  bool isNull() const { return true; }
};
struct JsonObjectConst { Field operator[](const char *name) const { return {name}; } };
Field::operator JsonObjectConst() const { return {}; }
bool plainJsonText(Field) { return true; }
bool validIdentifier(const char *text) { return text && *text; }
time_t deadline(const char *) { return time(nullptr) + 3600; }
size_t strlcpy(char *out, const char *in, size_t capacity) {
  size_t n = strlen(in);
  if (capacity) {
    size_t copy = n < capacity - 1 ? n : capacity - 1;
    memcpy(out, in, copy);
    out[copy] = '\0';
  }
  return n;
}
enum Phase { IDLE, EXECUTING, ACK_PENDING, BLOCKED };
struct Journal {
  char lock[201] = {}, command[65] = {};
  uint32_t generation = 0;
  Phase phase = IDLE;
} journal;
bool saved = true, RFID_PANEL_ON_MEGA = true, refreshed = false;
uint32_t lastRefresh = 0;
uint32_t millis() { return 1234; }
bool saveJournal() { return saved; }
int ack = 0, blocks = 0, rendered = 0;
void prepareAcknowledgement(bool success, const char *) { ack = success ? 1 : -1; }
void block(const char *) { ++blocks; journal.phase = BLOCKED; }
bool render(const LabelLayout &) { ++rendered; return true; }
namespace RfidBridge {
uint32_t boot = 1;
bool result = true, resetDuringDraw = false;
int draws = 0;
std::string displayed;
uint32_t megaBootCount() { return boot; }
bool draw(const String &text, bool status, uint32_t expectedBoot) {
  assert(!status && expectedBoot == boot);
  ++draws;
  displayed = text;
  if (resetDuringDraw) ++boot;
  return result;
}
}
'''
        assertions = r'''
void reset() {
  journal = Journal{};
  ack = blocks = rendered = RfidBridge::draws = 0;
  saved = true;
  RFID_PANEL_ON_MEGA = true;
  RfidBridge::result = true;
  RfidBridge::resetDuringDraw = false;
  description = "Description";
  ONLINE_ASSIGNMENTS_ENABLED = false;
  assignmentMatches = true;
  refreshed = false;
}
int main() {
  JsonObjectConst command;
  reset();
  execute(command);
  assert(RfidBridge::draws == 1 && RfidBridge::displayed == "Title | Collection | Description");
  assert(ack == 1 && blocks == 0 && refreshed && lastRefresh == 1234);
  reset();
  RfidBridge::result = false;
  execute(command);
  assert(RfidBridge::draws == 1 && ack == 0 && blocks == 1 && journal.phase == BLOCKED);
  reset();
  RfidBridge::resetDuringDraw = true;
  execute(command);
  assert(RfidBridge::draws == 1 && ack == 0 && blocks == 1);
  reset();
  saved = false;
  execute(command);
  assert(RfidBridge::draws == 0 && ack == 0);
  reset();
  description = "invalid\ntext";
  execute(command);
  assert(RfidBridge::draws == 0 && ack == -1);
  reset();
  RFID_PANEL_ON_MEGA = false;
  execute(command);
  assert(RfidBridge::draws == 0 && rendered == 1 && ack == 1);
  reset();
  ONLINE_ASSIGNMENTS_ENABLED = true;
  assignmentMatches = false;
  execute(command);
  assert(RfidBridge::draws == 0 && ack == 0);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "mega-cloud-execute.cpp"
            binary = Path(temp) / "mega-cloud-execute.exe"
            cpp.write_text(harness + execute + assertions, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_final_mega_cloud_label_format(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        source = r'''
#include <cassert>
#include <string>
#include "Final/ESP32S3_Final/LabelLayout.h"
int main() {
  char out[181];
  assert(formatMegaLabel("Vase", "Ceramics", "Made in 1920.", out, sizeof(out)));
  assert(std::string(out) == "Vase | Ceramics | Made in 1920.");
  assert(formatMegaLabel("Vase", "", "Made in 1920.", out, sizeof(out)));
  assert(std::string(out) == "Vase | Made in 1920.");
  std::string maximum(176, 'x');
  assert(formatMegaLabel("A", "", maximum.c_str(), out, sizeof(out)));
  assert(strlen(out) == 180);
  maximum += 'x';
  assert(!formatMegaLabel("A", "", maximum.c_str(), out, sizeof(out)));
  assert(!formatMegaLabel("A", "", "line\nbreak", out, sizeof(out)));
  assert(!formatMegaLabel("A", "", "Unicode: \xc3\xa9", out, sizeof(out)));
  assert(!formatMegaLabel("A", "", "", out, sizeof(out)));
  assert(!formatMegaLabel("A", nullptr, "Text", out, sizeof(out)));
  assert(!formatMegaLabel("A", "", "Text", out, 4));
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "mega-cloud-label.cpp"
            binary = Path(temp) / "mega-cloud-label.exe"
            cpp.write_text(source, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_warnings_layout_and_uart_compatibility(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        source = r'''
#include <cassert>
#include <string>
#include "firmware/ReShow/Mega2560_Sensors/WarningState.h"
#include "firmware/ReShow/ESP32S3_AWS/LabelLayout.h"
int main() {
  WarningState state;
  assert(state.update(2100, 400, true, false, 0) == 0);
  // Transient breaches clear their timer, including independent light/temperature.
  assert(state.update(2500, 700, true, false, 100) == 0);
  assert(state.update(2100, 700, true, false, 4000) == 0);
  assert(state.update(2500, 700, true, false, 5100) == LIGHT_HIGH);
  assert(state.update(2500, 400, true, false, 10100) == TEMP_HIGH);
  assert(state.update(2100, 400, true, false, 10101) == 0);
  // 22C caution / 24C high; low bound is exclusive, light endpoints inclusive.
  WarningState bounds;
  bounds.update(2200, 300, true, false, 0);
  assert(bounds.update(2200, 600, true, false, 5000) == TEMP_CAUTION);
  assert(bounds.update(2400, 600, true, false, 5001) == 0);
  assert(bounds.update(2400, 600, true, false, 10001) == TEMP_HIGH);
  assert(bounds.update(1800, 600, true, false, 10002) == 0);
  bounds.update(1799, 299, true, false, 10003);
  assert(bounds.update(1799, 299, true, false, 15003) == (TEMP_LOW | LIGHT_LOW));
  // Unknown sensors are faults, never a false temperature-low reading.
  assert(bounds.update(-32768, 400, false, true, 15004) ==
         (TEMP_FAULT | SONAR_FAULT | TEMP_SWING));
  assert(bounds.update(2100, 400, true, false, 15005) == 0);
  WarningState wrap;
  wrap.update(2500, 700, true, false, UINT32_MAX - 2000);
  assert(wrap.update(2500, 700, true, false, 2998) == 0);
  assert(wrap.update(2500, 700, true, false, 2999) == (TEMP_HIGH | LIGHT_HIGH));
  LabelLayout layout;
  assert(makeLabelLayout("Blue ceramic vase", "City collection",
                         "A hand-painted vase made in 1920.\nPlease do not touch.", layout));
  assert(layout.ruleAfter == 2 && layout.used <= LABEL_ROWS);
  assert(std::string(layout.lines[0]) == "Blue ceramic vase");
  // Word boundaries, exact-width words, empty collection and explicit newlines.
  std::string word(31, 'a');
  assert(makeLabelLayout(word.c_str(), "", "line one\nline two", layout));
  assert(std::string(layout.lines[0]) == word && layout.used == 4);
  std::string full(620, 'x'); // 20 description rows + title + separator = 22.
  assert(makeLabelLayout("Title", "", full.c_str(), layout));
  assert(layout.used == LABEL_ROWS);
  full += 'x';
  assert(!makeLabelLayout("Title", "", full.c_str(), layout));
  assert(!makeLabelLayout("Title", "", std::string(2001, 'x').c_str(), layout));
  assert(!makeLabelLayout("Title", "", "Unicode: \xc3\xa9", layout));
  assert(!makeLabelLayout("Title", "", "bad\tcontrol", layout));
  assert(!makeLabelLayout(" ", "", "Description", layout));
  assert(!makeLabelLayout("Title", "", "", layout));
  assert(!makeLabelLayout(nullptr, "", "Description", layout));
  // Every rendered row stays within both the array and physical screen bounds.
  assert(makeLabelLayout("A long title with several words split over lines",
                         "A collection", "A description.", layout));
  for (unsigned i = 0; i < layout.used; ++i) {
    assert(strlen(layout.lines[i]) <= LABEL_COLUMNS);
    assert(7 + strlen(layout.lines[i]) * 6 <= 193);
    assert(8 + i * 8 + 8 <= 184);
  }
}
'''
        mega = (ROOT / "firmware/ReShow/Mega2560_Sensors/Mega2560_Sensors.ino").read_text()
        esp = (ROOT / "firmware/ReShow/ESP32S3_AWS/ESP32S3_AWS.ino").read_text()
        uart = r'''
#include <sstream>
#include <cstdio>
#include <cstdlib>
#include <cerrno>
#include <cctype>
#define F(value) value
namespace MegaFixture {
struct Port {
  std::ostringstream data;
  template<typename T> void print(T value) { data << value; }
  template<typename T> void println(T value) { data << value << "\r\n"; }
} Serial, Serial2;
uint32_t millis() { return 123456; }
uint32_t sequenceNumber = 0, eventCount = 12;
int16_t temperatureCenti = 2310, distanceMm = 123;
uint16_t lightRaw = 500;
uint8_t lightingMode = 0;
bool occupancyKnown = true, occupied = true, lightsOn = true;
'''
        uart += function(mega, "uint16_t crc16(") + "\n" + function(mega, "void sendSample(")
        uart += "\n}\nnamespace EspFixture {\n"
        uart += function(esp, "struct Sample {") + ";\n"
        uart += function(esp, "uint16_t crc16(") + "\n"
        uart += function(esp, "bool parseFrame(char *line, Sample &s) {")
        uart += r'''
}
void uartRegression() {
  using namespace MegaFixture;
  sendSample();
  std::string line = Serial2.data.str();
  assert(line.size() < 160 && line.substr(line.size() - 2) == "\r\n");
  line.resize(line.size() - 2);
  char wire[160];
  strcpy(wire, line.c_str());
  EspFixture::Sample parsed = {};
  assert(EspFixture::parseFrame(wire, parsed));
  assert(parsed.sequence == 1 && parsed.uptimeMs == 123456);
  assert(parsed.temperatureCenti == 2310 && parsed.lightRaw == 500);
  assert(parsed.distanceMm == 123 && parsed.occupied == 1 && parsed.events == 12);
  assert(parsed.mode == 0 && parsed.output);
  strcpy(wire, line.c_str()); wire[10] ^= 1;
  assert(!EspFixture::parseFrame(wire, parsed));
  Serial2.data.str("");
  temperatureCenti = -32768; occupancyKnown = false; distanceMm = -1;
  sendSample();
  line = Serial2.data.str(); line.resize(line.size() - 2);
  strcpy(wire, line.c_str());
  assert(EspFixture::parseFrame(wire, parsed));
  assert(parsed.temperatureCenti == -32768 && parsed.distanceMm == -1 && parsed.occupied == -1);
}
'''
        source = source.replace("int main() {", uart + "\nint main() {\n  uartRegression();")
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "displays.cpp"
            binary = Path(temp) / "displays.exe"
            cpp.write_text(source, encoding="utf-8")
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-static", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                           check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
