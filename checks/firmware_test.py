"""Exercise the sketch's upload state machine on a host, without network or boards."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKETCH = ROOT / "firmware/ReShow/ESP32S3_AWS/ESP32S3_AWS.ino"


def function(source, signature):
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class FirmwareTests(unittest.TestCase):
    def test_upload_retention_and_default_gate(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            local_compiler = Path("C:/msys64/ucrt64/bin/g++.exe")
            if local_compiler.is_file():
                compiler = str(local_compiler)
        if not compiler:
            self.skipTest("A host C++ compiler is required")

        source = SKETCH.read_text(encoding="utf-8")
        gate = next(line for line in source.splitlines()
                    if line.startswith("constexpr bool HARDWARE_UPLOADS_ENABLED"))
        functions = "\n".join(function(source, signature) for signature in (
            "bool validIdentifier(", "void finishStage(",
            "void handleUploadResult(", "bool uploadConfigurationReady(",
        ))
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cctype>
#include <string>
using String = std::string;
struct SerialMock {
  void println(const char *) {}
  template<typename... Args> void printf(const char *, Args...) {}
} Serial;
uint32_t millis() { return 1234; }
uint32_t retryDelayMs = 2000, nextAttempt = 0;
unsigned pendingStage = 0;
bool havePending = true;
char pendingJson[256];
// Deliberately configured fixtures: the default gate must still prevent uploads.
const char *WIFI_SSID = "fixture-network", *WIFI_PASSWORD = "fixture-password";
const char *DEVICE_BEARER_TOKEN = "fixture-device-token";
const char *DEVICE_ID = "fixture-device", *EXHIBIT_ID = "fixture-exhibit";
'''
        assertions = r'''
int main() {
  assert(!uploadConfigurationReady());
  for (unsigned stage : {0u, 1u}) {
    pendingStage = stage;
    havePending = true;
    strcpy(pendingJson, "{\"id\":\"stable-id\",\"source\":\"device\"}");
    const std::string original = pendingJson;
    // Conflicts must not advance either endpoint or mutate the retry payload.
    handleUploadResult(409, "");
    assert(havePending && pendingStage == stage && pendingJson == original);
    assert(nextAttempt == millis() + 60000);
    handleUploadResult(409, "");
    assert(havePending && pendingStage == stage && pendingJson == original);
    // Other transient failures must retain the same record as well.
    handleUploadResult(429, "120");
    assert(nextAttempt == millis() + 120000);
    handleUploadResult(503, "");
    assert(havePending && pendingStage == stage && pendingJson == original);
    // Only an accepted record advances to the paired stage/completes the sample.
    handleUploadResult(stage == 0 ? 200 : 201, "");
    assert(pendingStage == stage + 1 && pendingJson[0] == '\0');
    assert(havePending == (stage == 0) && retryDelayMs == 2000);
  }
  pendingStage = 0;
  havePending = true;
  strcpy(pendingJson, "invalid schema");
  handleUploadResult(422, "");
  assert(pendingStage == 1 && havePending && pendingJson[0] == '\0');
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "firmware.cpp"
            binary = Path(temp) / "firmware-test.exe"
            cpp.write_text(harness + gate + "\n" + functions + assertions,
                           encoding="utf-8")
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                            "-static", str(cpp), "-o", str(binary)],
                           check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
