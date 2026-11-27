"""Check merged Wi-Fi startup independently of AWS using the actual functions."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from checks.firmware_test import function

ROOT = Path(__file__).resolve().parents[1]
SKETCH = ROOT / "hardware_updated/Final/ESP32S3_Final/ESP32S3_Final.ino"


class WifiStartupTests(unittest.TestCase):
    def test_credentials_startup_and_connection_reporting(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("Host C++ compiler required")
        source = SKETCH.read_text(encoding="utf-8")
        functions = "\n".join(function(source, signature) for signature in (
            "bool validIdentifier(", "bool backendConfigurationReady() {", "bool uploadConfigurationReady() {",
            "bool wifiConfigurationReady() {", "void startWifi() {",
            "bool serviceWifi() {",
        ))
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cctype>
#include <string>
const char *WIFI_SSID = "fixture-hotspot", *WIFI_PASSWORD = "fixture-pass";
const char *DEVICE_BEARER_TOKEN = "REPLACE_DEVICE_BEARER_TOKEN";
const char *DEVICE_ID = "fixture-device", *EXHIBIT_ID = "fixture-exhibit";
bool HARDWARE_UPLOADS_ENABLED = false;
bool wifiConfigured = false, wifiConnectionReported = false;
uint32_t lastWifiStatus = 0, clockMs = 0;
uint32_t millis() { return clockMs; }
struct Log {
  std::string text;
  void println(const char *s) { text += std::string(s) + "\n"; }
  template<typename... T> void printf(const char *f, T... args) {
    char buffer[512]; snprintf(buffer, sizeof(buffer), f, args...); text += buffer;
  }
} Serial;
enum wl_status_t { WL_IDLE_STATUS = 0, WL_CONNECTED = 3, WL_DISCONNECTED = 6 };
constexpr int WIFI_STA = 1;
void wifiEvent(int, int) {}
struct Address { std::string toString() { return "192.0.2.10"; } };
struct WifiMock {
  int starts = 0, callbacks = 0, modeSet = 0;
  bool autoReconnect = false;
  wl_status_t state = WL_IDLE_STATUS;
  void onEvent(void (*)(int,int)) { ++callbacks; }
  void mode(int mode) { modeSet = mode; }
  void setAutoReconnect(bool value) { autoReconnect = value; }
  void begin(const char *ssid, const char *password) {
    assert(!strcmp(ssid, WIFI_SSID) && !strcmp(password, WIFI_PASSWORD)); ++starts;
  }
  wl_status_t status() { return state; }
  Address localIP() { return {}; }
  long RSSI() { return -50; }
} WiFi;
'''
        assertions = r'''
int main() {
  // Neither a disabled AWS flag nor a missing hardware token can prevent Wi-Fi.
  assert(!uploadConfigurationReady());
  startWifi();
  assert(wifiConfigured && WiFi.starts == 1 && WiFi.autoReconnect);
  assert(WiFi.modeSet == WIFI_STA && WiFi.callbacks == 1);
  HARDWARE_UPLOADS_ENABLED = true;
  assert(!uploadConfigurationReady() && wifiConfigurationReady());
  DEVICE_BEARER_TOKEN = "fixture-token";
  assert(uploadConfigurationReady());
  HARDWARE_UPLOADS_ENABLED = false;
  assert(backendConfigurationReady() && !uploadConfigurationReady());
  HARDWARE_UPLOADS_ENABLED = true;
  // Waiting does not restart an in-progress association/DHCP attempt.
  for (clockMs = 0; clockMs <= 60000; clockMs += 5000) assert(!serviceWifi());
  assert(WiFi.starts == 1 && Serial.text.find("WIFI WAIT") != std::string::npos);
  WiFi.state = WL_CONNECTED;
  assert(serviceWifi() && wifiConnectionReported);
  size_t length = Serial.text.size();
  assert(serviceWifi() && Serial.text.size() == length);
  WiFi.state = WL_DISCONNECTED;
  assert(!serviceWifi() && !wifiConnectionReported);
  WiFi.state = WL_CONNECTED;
  assert(serviceWifi() && wifiConnectionReported && Serial.text.size() > length);
  assert(Serial.text.find("fixture-pass") == std::string::npos);
  assert(Serial.text.find("fixture-token") == std::string::npos);
  WIFI_SSID = "REPLACE_WIFI_SSID";
  startWifi(); assert(!wifiConfigured && WiFi.starts == 1);
  WIFI_SSID = ""; assert(!wifiConfigurationReady());
  WIFI_SSID = "fixture-hotspot"; WIFI_PASSWORD = "REPLACE_WIFI_PASSWORD";
  assert(!wifiConfigurationReady());
  WIFI_PASSWORD = ""; assert(wifiConfigurationReady()); // Open test network.
  WIFI_SSID = "12345678901234567890123456789012345";
  assert(!wifiConfigurationReady());
  WIFI_SSID = "fixture-hotspot";
  std::string oversized(65, 'x'); WIFI_PASSWORD = oversized.c_str();
  assert(!wifiConfigurationReady());
  lastWifiStatus = UINT32_MAX - 2000; clockMs = 2999;
  wifiConfigured = true; WiFi.state = WL_DISCONNECTED;
  assert(!serviceWifi() && lastWifiStatus == 2999); // millis wrap.
}
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "wifi.cpp"
            exe = Path(temp) / "wifi.exe"
            cpp.write_text(harness + functions + assertions, encoding="utf-8")
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-static", str(cpp), "-o", str(exe)], check=True,
                           capture_output=True, text=True, timeout=60)
            subprocess.run([str(exe)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
