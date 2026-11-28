"""Regression checks for the restored ESP parser, payload and upload retries."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from checks.firmware_test import function

ROOT = Path(__file__).resolve().parents[1]


class RecoveredEspTests(unittest.TestCase):
    def test_frames_payloads_and_retained_uploads(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("Host C++ compiler required")
        source = (ROOT / "hardware_updated/Final/ESP32S3_Final/ESP32S3_Final.ino").read_text()
        prefix = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <cerrno>
#include <cctype>
#include <string>
#include "hardware_updated/Final/ESP32S3_Final/ProfileProtocol.h"
using String = std::string;
struct Port {
  void println(const char *) {}
  template<typename... T> void printf(const char *, T...) {}
} Serial;
const char *DEVICE_ID = "fixture-device", *EXHIBIT_ID = "fixture-exhibit";
uint32_t retryDelayMs = 2000, nextAttempt = 0;
uint8_t pendingStage = 0;
bool havePending = true;
char pendingJson[768];
uint32_t millis() { return 1000; }
'''
        code = function(source, "struct Sample {") + ";\n"
        code += "\n".join(function(source, signature) for signature in (
            "uint16_t crc16(", "bool parseFrame(char *line, Sample &s) {",
            "bool makePayload(const Sample &s, bool status, char *out, size_t capacity) {",
            "void finishStage() {", "void handleUploadResult(",
        ))
        assertions = r'''
bool frame(const char *body, Sample &s) {
  char line[160]; snprintf(line, sizeof(line), "%s*%04X", body, crc16(body));
  return parseFrame(line, s);
}
int main() {
  Sample s = {};
  assert(frame("V1,1,10000,2310,500,123,1,2,0,1", s));
  assert(s.temperatureCenti == 2310 && s.distanceMm == 123 && s.occupied == 1);
  strcpy(s.id, "fixture-record"); strcpy(s.timestamp, "2026-11-23T10:00:00.000Z");
  char json[768];
  assert(makePayload(s, false, json, sizeof(json)));
  assert(strstr(json, "\"temperature_c\":23.10") && strstr(json, "\"distance_cm\":12.3"));
  assert(!makePayload(s, false, json, 8));
  assert(frame("V1,2,20000,-32768,500,-1,-1,2,0,0", s));
  assert(makePayload(s, true, json, sizeof(json)));
  assert(strstr(json, "\"temperature_c\":null") && strstr(json, "\"occupied\":null"));
  assert(!makePayload(s, false, json, sizeof(json)));
  // Requested no-echo policy: empty occupancy can be uploaded with null distance.
  assert(frame("V1,3,30000,2310,500,-1,0,2,0,0", s));
  assert(makePayload(s, false, json, sizeof(json)));
  assert(strstr(json, "\"occupied\":false") && strstr(json, "\"distance_cm\":null"));
  assert(!frame("V1,3,30000,2310,500,-1,1,2,0,0", s));
  assert(!frame("V1,3,30000,2310,500,123,-1,2,0,0", s));
  assert(!frame("V1,3,30000,-50,500,123,1,2,0,0", s));
  char bad[] = "V1,3,30000,2310,500,123,1,2,0,0*0000";
  assert(!parseFrame(bad, s));
  assert(frame("V2,4,40000,2310,500,123,1,2,0,1,vase-002,7", s));
  assert(s.profileGeneration == 7 && !strcmp(s.exhibitId, "vase-002"));
  Sample queued = s;
  assert(frame("V2,5,50000,2310,500,123,1,2,0,1,clock-003,9", s));
  assert(makePayload(queued, false, json, sizeof(json)));
  assert(strstr(json, "\"exhibit_id\":\"vase-002\""));
  assert(makePayload(s, false, json, sizeof(json)));
  assert(strstr(json, "\"exhibit_id\":\"clock-003\""));
  assert(!frame("V2,6,60000,2310,500,123,1,2,0,1,bad/id,10", s));
  assert(!frame("V2,6,60000,2310,500,123,1,2,0,1,vase-002,1000000000", s));
  for (uint8_t stage : {0, 1}) {
    for (int code : {401, 403, 404, 409, 429, 503, -1}) {
      pendingStage = stage; havePending = true;
      strcpy(pendingJson, "stable fixture payload");
      handleUploadResult(code, code == 429 ? "120" : "");
      assert(havePending && pendingStage == stage);
      assert(!strcmp(pendingJson, "stable fixture payload"));
      if (code == 429) assert(nextAttempt >= millis() + 120000);
    }
    handleUploadResult(201, "");
    assert(pendingStage == stage + 1 && havePending == (stage == 0));
    assert(!pendingJson[0]);
  }
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "recovery.cpp"
            exe = Path(temp) / "recovery.exe"
            cpp.write_text(prefix + code + assertions, encoding="utf-8")
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-static", "-I", str(ROOT), str(cpp), "-o", str(exe)], check=True,
                           capture_output=True, text=True, timeout=60)
            subprocess.run([str(exe)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
