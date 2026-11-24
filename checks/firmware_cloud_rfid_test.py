"""Exercise the Final cloud RFID payload and retry logic without a device."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from checks.firmware_test import function


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "Final/ESP32S3_Final/RfidBridge.h"


@unittest.skipUnless(BRIDGE.is_file(), "The optional Final firmware is not present")
class CloudRfidTests(unittest.TestCase):
    def test_contract_and_independent_retry(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler and Path("C:/msys64/ucrt64/bin/g++.exe").is_file():
            compiler = "C:/msys64/ucrt64/bin/g++.exe"
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        source = BRIDGE.read_text(encoding="utf-8")
        helpers = "\n".join(function(source, name) for name in (
            "bool lanEnabled(", "void queueCloudEvent(", "bool makeCloudPayload(",
            "bool timestampCloudEvent(", "void tickCloud(",
        ))
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <sys/time.h>
class String : public std::string {
 public:
  using std::string::string;
  String() = default;
};
struct CloudEvent {
  char id[64], timestamp[28], result[9], role[7];
  uint32_t receivedAtMs;
};
struct Queue {
  CloudEvent event;
  bool present = true;
} queue;
Queue *cloudEvents = &queue;
bool xQueuePeek(Queue *q, CloudEvent *event, int) {
  if (!q->present) return false;
  *event = q->event;
  return true;
}
constexpr int pdTRUE = 1;
int xQueueSend(Queue *q, CloudEvent *event, int) {
  if (q->present) return 0;
  q->event = *event;
  q->present = true;
  return pdTRUE;
}
int xQueueReceive(Queue *q, CloudEvent *event, int) {
  if (!q->present) return 0;
  *event = q->event;
  q->present = false;
  return pdTRUE;
}
struct SerialMock {
  void println(const char *) {}
  template<typename... Args> void printf(const char *, Args...) {}
} Serial;
const bool CLOUD_RFID_ENABLED = true;
const bool LOCAL_RFID_ENABLED = false;
const char *RFID_SERVER_ORIGIN = "http://bench-server.invalid";
const char *DEVICE_ID = "test-device", *DEVICE_BEARER_TOKEN = "test-token";
const char *API_ORIGIN = "https://example.invalid", *AMAZON_ROOT_CA1 = "test-ca";
const char *bootId = "test-boot";
CloudEvent pendingCloudEvent = {};
bool havePendingCloudEvent = false;
const time_t MIN_VALID_EPOCH = 1767225600;
uint32_t cloudSequence = 0;
time_t fakeSeconds = 1795492800;
int fakeGetTimeOfDay(struct timeval *value, void *) {
  value->tv_sec = fakeSeconds;
  value->tv_usec = 123000;
  return 0;
}
#define gettimeofday fakeGetTimeOfDay
#ifdef _WIN32
struct tm *gmtime_r(const time_t *value, struct tm *out) {
  return gmtime_s(out, value) == 0 ? out : nullptr;
}
#endif
size_t strlcpy(char *out, const char *in, size_t capacity) {
  size_t n = strlen(in);
  if (capacity) {
    size_t copy = n < capacity - 1 ? n : capacity - 1;
    memcpy(out, in, copy);
    out[copy] = '\0';
  }
  return n;
}
struct JsonField {
  const char *value;
  const char *operator|(const char *fallback) const { return value ? value : fallback; }
};
struct JsonDocument {
  const char *result;
  const char *role;
  JsonField operator[](const char *key) {
    return {strcmp(key, "result") == 0 ? result : role};
  }
};
bool configured = true;
bool cloudConfigured() { return configured; }
uint32_t now = 1000, nextCloudEvent = 0, cloudRetryMs = 2000;
uint32_t millis() { return now; }
int status = 503;
std::string sent;
String retryHeader;
struct WiFiClientSecure {
  void setCACert(const char *) {}
  void setHandshakeTimeout(int) {}
};
constexpr int HTTPC_DISABLE_FOLLOW_REDIRECTS = 0;
struct HTTPClient {
  void setConnectTimeout(int) {}
  void setTimeout(int) {}
  void setFollowRedirects(int) {}
  bool begin(WiFiClientSecure &, const std::string &) { return true; }
  void collectHeaders(const char **, int) {}
  void addHeader(const char *, const char *) {}
  void addHeader(const char *, const std::string &) {}
  int POST(uint8_t *body, size_t size) {
    sent.assign(reinterpret_cast<char *>(body), size);
    return status;
  }
  String header(const char *) { return retryHeader; }
  void end() {}
};
'''
        assertions = r'''
int main() {
  assert(!lanEnabled());
  queue.present = false;
  JsonDocument tap = {"accepted", "door"};
  fakeSeconds = 0;
  now = 1000;
  queueCloudEvent(tap);
  assert(queue.present && queue.event.timestamp[0] == '\0');
  const CloudEvent captured = queue.event;
  tap.role = "unknown";
  queueCloudEvent(tap);
  assert(queue.present && std::string(queue.event.id) == captured.id);
  assert(std::string(queue.event.id) == "rfid-test-boot-00000001");
  assert(std::string(queue.event.timestamp).empty());
  tap.role = "door";
  fakeSeconds = 1795492800;
  now = 11000;

  CloudEvent accepted = {};
  status = 503;
  tickCloud();
  assert(havePendingCloudEvent && !queue.present);
  assert(std::string(pendingCloudEvent.timestamp) == "2026-11-24T03:59:50.123Z");
  const std::string first = sent;
  assert(first.find("\"event_id\":\"rfid-test-boot-00000001\"") != std::string::npos);
  assert(first.find("\"recorded_at\":\"2026-11-24T03:59:50.123Z\"") != std::string::npos);

  accepted = {"rfid-test-1", "2026-11-24T04:00:00.000Z", "accepted", "door", 0};
  queue.present = false;
  tap = {"denied", nullptr};
  queueCloudEvent(tap);
  assert(queue.present && queue.event.role[0] == '\0');
  CloudEvent denied = {"rfid-test-2", "2026-11-24T04:00:01.000Z", "denied", ""};
  char payload[256];
  assert(makeCloudPayload(accepted, payload, sizeof(payload)));
  puts(payload);
  assert(makeCloudPayload(denied, payload, sizeof(payload)));
  puts(payload);
  assert(!makeCloudPayload(accepted, payload, 10));

  now = nextCloudEvent;
  status = 401;
  tickCloud();
  assert(havePendingCloudEvent && nextCloudEvent == now + 60000 && sent == first);
  now = nextCloudEvent;
  status = 409;
  tickCloud();
  assert(havePendingCloudEvent && nextCloudEvent == now + 60000 && sent == first);
  now = nextCloudEvent;
  status = 429;
  retryHeader = "120";
  tickCloud();
  assert(havePendingCloudEvent && nextCloudEvent == now + 120000 && sent == first);
  now = nextCloudEvent;
  status = 200;
  tickCloud();
  assert(!havePendingCloudEvent && cloudRetryMs == 2000 && sent == first);
  queue.event = denied;
  queue.present = true;
  now = nextCloudEvent;
  status = 201;
  tickCloud();
  assert(!queue.present && !havePendingCloudEvent);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "cloud-rfid.cpp"
            binary = Path(temp) / "cloud-rfid.exe"
            cpp.write_text(harness + helpers + assertions, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-static", str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)

        accepted, denied = map(json.loads, result.stdout.splitlines())
        schema = json.loads((ROOT / "backend/backend/openapi.json").read_text(encoding="utf-8"))
        schema = schema["components"]["schemas"]["RfidEventInput"]
        for event in (accepted, denied):
            self.assertEqual(set(event) - set(schema["properties"]), set())
            self.assertTrue(set(schema["required"]).issubset(event))
            self.assertEqual(event["source"], "device")
            for name in ("event_id", "device_id"):
                self.assertRegex(event[name], schema["properties"][name]["pattern"])
        self.assertEqual(accepted["role"], "door")
        self.assertNotIn("role", denied)


if __name__ == "__main__":
    unittest.main()
