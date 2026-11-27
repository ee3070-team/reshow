"""Exercise the final ESP loop's display fetch gates and the Mega OLED output."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from checks.firmware_test import function

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "hardware_updated/Final"


class DisplayFetchTests(unittest.TestCase):
    def compile_run(self, code):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("Host C++ compiler required")
        with tempfile.TemporaryDirectory() as directory:
            cpp, exe = Path(directory) / "test.cpp", Path(directory) / "test.exe"
            cpp.write_text(code, encoding="utf-8")
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-static", "-I", str(ROOT), str(cpp), "-o", str(exe)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            subprocess.run([str(exe)], check=True, timeout=10)

    def test_warning_led_priority_and_restoration(self):
        source = (FINAL / "Mega_Final/Mega_Final.ino").read_text()
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
constexpr int HIGH=1, LOW=0;
const uint8_t LED_PINS[]={40,42,38,36,34,32};
bool LED_ACTIVE_HIGH=true, lightsOn=false, occupancyKnown=true, occupied=false;
bool epaperCardFeedbackActive=false;
uint8_t lightingMode=0;
uint16_t warningMask=0;
uint32_t clockMs=0, epaperCardFeedbackStartedAt=0;
const uint32_t epaperCardFeedbackDuration=1000;
uint32_t millis() { return clockMs; }
int pins[54]={};
void digitalWrite(uint8_t pin,int value) { pins[pin]=value; }
void expect(bool red,bool green,bool blue) {
  const bool expected[]={red,green,blue,red,green,blue};
  for(int i=0;i<6;++i)
    assert(pins[LED_PINS[i]]==(expected[i]==LED_ACTIVE_HIGH ? HIGH : LOW));
  assert(lightsOn==(red||green||blue));
}
''' + function(source, "void updateLighting() {") + r'''
int main() {
  for(bool activeHigh : {true,false}) {
    LED_ACTIVE_HIGH=activeHigh;
    for(uint8_t mode=0;mode<3;++mode) {
      lightingMode=mode; occupied=false; clockMs=0;
      epaperCardFeedbackStartedAt=0; epaperCardFeedbackActive=true;
      // Every warning bit, including sensor faults, overrides all modes/green.
      for(uint16_t bit=1;bit<=128;bit<<=1) {
        warningMask=bit; updateLighting(); expect(true,false,false);
        assert(lightingMode==mode);
      }
      warningMask=255; updateLighting(); expect(true,false,false);
      warningMask=0; updateLighting(); expect(false,true,false);
      clockMs=1000; updateLighting(); expect(mode==1,mode==1,mode==1);
      assert(!epaperCardFeedbackActive);
    }
    lightingMode=0; occupied=true; updateLighting(); expect(true,true,true);
    warningMask=2; updateLighting(); expect(true,false,false);
    warningMask=0; updateLighting(); expect(true,true,true);
    occupied=false; updateLighting(); expect(false,false,false);
    // Feedback still expires while a warning owns the LEDs, including rollover.
    epaperCardFeedbackActive=true; epaperCardFeedbackStartedAt=UINT32_MAX-499;
    clockMs=500; warningMask=64; updateLighting(); expect(true,false,false);
    assert(!epaperCardFeedbackActive);
    warningMask=0; updateLighting(); expect(false,false,false);
  }
}
''')

    def test_fetch_without_uploads_and_without_samples(self):
        source = (FINAL / "ESP32S3_Final/ESP32S3_Final.ino").read_text()
        functions = "\n".join(function(source, signature) for signature in (
            "void fetchWarningData() {", "void fetchExhibitData() {", "void loop() {"))
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
#include <ctime>
#include <string>
uint32_t millis() { return 10000; }
void delay(int) {}
bool wifiConfigured=true, backendReady=true, configurationReady=false;
bool ntpStarted=false, HARDWARE_UPLOADS_ENABLED=false, havePending=false;
bool connected=true;
bool serviceWifi() { return connected; }
const char *CONFIG_SOURCE="fixture";
const time_t MIN_VALID_EPOCH=0;
void configTime(int,int,const char *,const char *) {}
struct Log {
  void println(const char *) {}
  template<class... T> void printf(const char *, T...) {}
} Serial;
struct Wifi { int status() { return 3; } } WiFi;
std::string order;
namespace OnlineProfile { bool ready=true; void tick() { order+='W'; } }
namespace OnlineLabel { bool ready=true; void tick() { order+='E'; } }
namespace RfidBridge { void tick() {} }
uint32_t nextAttempt=0;
int sampleQueue=0, pending=0, pendingStage=0, uploads=0;
const int pdTRUE=1;
char pendingJson[10]="";
int xQueueReceive(int,int *,int) { return 0; }
bool makePayload(int,bool,char *,size_t) { return true; }
void finishStage() {}
void uploadPending() { ++uploads; }
''' + functions + r'''
int main() {
  // Wi-Fi + credentials, telemetry disabled: both fetch paths still run.
  loop(); assert(order=="WE" && ntpStarted && uploads==0);
  order.clear(); configurationReady=true;
  loop(); assert(order=="WE" && uploads==0); // No sensor samples required.
  order.clear(); nextAttempt=60000;
  loop(); assert(order=="WE"); // Telemetry backoff does not delay downloads.
  order.clear(); backendReady=false;
  loop(); assert(order.empty()); // Never send an unconfigured bearer token.
  backendReady=true; connected=false;
  loop(); assert(order.empty());
  connected=true; wifiConfigured=false;
  loop(); assert(order.empty());
}
''')

    def test_oled_reports_downloaded_limits_and_prioritizes_warnings(self):
        source = (FINAL / "Mega_Final/Mega_Final.ino").read_text()
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>
#define F(x) x
#define SSD1306_WHITE 1
#include "hardware_updated/Final/Mega_Final/ProfileProtocol.h"
ExhibitProfile appliedProfile;
bool oledReady=true, occupancyKnown=true, occupied=false;
uint16_t warningMask=0, lightRaw=400;
int16_t temperatureCenti=2100;
const int16_t INVALID_TEMP=-32768;
uint32_t millis() { return 2000; }
struct Screen {
  std::vector<std::string> lines;
  std::string current;
  void clearDisplay() { lines.clear(); current.clear(); }
  void setTextSize(int) {} void setTextColor(int) {}
  void setTextWrap(bool) {} void setCursor(int,int) {} void display() {}
  void print(const char *s) { current+=s; }
  void print(char c) { current+=c; }
  void print(unsigned long n) { current+=std::to_string(n); }
  void print(uint16_t n) { current+=std::to_string(n); }
  void print(double n,int digits) { char b[24]; snprintf(b,sizeof(b),"%.*f",digits,n); current+=b; }
  void println(const char *s) { print(s); lines.push_back(current); current.clear(); }
} oled;
struct Bus { bool getWireTimeoutFlag() { return false; } void clearWireTimeoutFlag() {} } Wire;
struct Log { void println(const char *) {} } Serial;
''' + function(source, "void drawOled() {") + r'''
int main() {
  appliedProfile.generation=1; appliedProfile.temperatureMin=2010;
  appliedProfile.temperatureMax=2600; appliedProfile.lightMin=307;
  appliedProfile.lightMax=716; appliedProfile.holdMs=2000;
  drawOled(); assert(oled.lines.size()==8);
  assert(oled.lines[4]=="OK / fetched limits");
  assert(oled.lines[5]=="T: 20.10-26.00 C");
  assert(oled.lines[6]=="L: 307-716 ADC");
  assert(oled.lines[7]=="Hold: 2 sec");
  for(const auto &line:oled.lines) assert(line.size()<=21);
  warningMask=2|16; drawOled();
  assert(oled.lines[4]=="E02 TEMP HIGH" && oled.lines[5]=="E03 LIGHT HIGH");
  warningMask=255; drawOled(); assert(oled.lines.size()==8);
  warningMask=0; appliedProfile.generation=0; drawOled();
  assert(oled.lines[4]=="OK / bench limits");
}
''')


if __name__ == "__main__":
    unittest.main()
