"""Run the actual final firmware profile/transfer code with host peripherals."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from checks.firmware_test import function

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "hardware_updated/Final"


class ItemSwitchTests(unittest.TestCase):
    def compile_run(self, source, extra_includes=()):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("Host C++ compiler required")
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            (temp / "EEPROM.h").write_text("#pragma once\n")
            cpp, exe = temp / "test.cpp", temp / "test.exe"
            cpp.write_text(source, encoding="utf-8")
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-static",
                       "-I", str(ROOT), "-I", str(temp)]
            for include in extra_includes:
                command += ["-I", str(include)]
            result = subprocess.run(command + [str(cpp), "-o", str(exe)], capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wire_copies_match(self):
        self.assertEqual((FINAL / "Mega_Final/ProfileProtocol.h").read_bytes(),
                         (FINAL / "ESP32S3_Final/ProfileProtocol.h").read_bytes())

    def test_mega_persistence_thresholds_and_transfers(self):
        panel = function((FINAL / "Mega_Final/MegaPanel.h").read_text(), "void checkEpaperResponse() {")
        prefix = r'''
#include <cassert>
#include <array>
#include <string>
#include <sstream>
#include <stdexcept>
#include <cstdint>
#include <cstring>
#define F(x) x
struct Memory {
  std::array<uint8_t,4096> bytes{};
  int remaining = -1;
  uint8_t read(int i) { return bytes.at(i); }
  void update(int i, uint8_t v) {
    if (remaining == 0) throw std::runtime_error("power loss");
    if (remaining > 0) --remaining;
    bytes.at(i) = v;
  }
} EEPROM;
struct Port {
  std::string input;
  size_t offset = 0;
  std::ostringstream output;
  int available() { return offset < input.size(); }
  char read() { return input[offset++]; }
  template<class T> void print(T value) { output << value; }
  template<class T> void println(T value) { output << value << "\n"; }
  void println() { output << "\n"; }
} Serial, Serial2;
#include "hardware_updated/Final/Mega_Final/AppliedProfile.h"
#include "hardware_updated/Final/Mega_Final/WarningState.h"
WarningState warnings;
int changed = 0, draws = 0;
void profileChanged() { ++changed; warnings.apply(appliedProfile); }
bool drawMegaPanel(const char *) { ++draws; return true; }
void reboot() { appliedProfile = ExhibitProfile{}; appliedWire[0] = 0; profileSlot = -1; loadAppliedProfile(); }
void line(const std::string &s) {
  char buffer[64]; assert(s.size() < sizeof(buffer)); strcpy(buffer, s.c_str());
  assert(handleProfileLine(buffer));
}
void transfer(const std::string &wire, bool badCrc = false) {
  line("CFG:42:B:" + std::to_string(wire.size()) + "," + std::to_string(profileCrc(wire.c_str()) ^ (badCrc ? 1 : 0)));
  for (size_t i = 0; i < wire.size(); i += 24)
    line("CFG:42:" + std::to_string(i/24) + ":" + wire.substr(i,24));
  line("CFG:42:E:" + std::to_string(profileCrc(wire.c_str())));
}
'''
        assertions = r'''
int main() {
  ExhibitProfile p; p.generation=1; p.version=2;
  strcpy(p.exhibit,"vase-002"); strcpy(p.tag,"EX-VASE"); strcpy(p.command,"assign-1");
  p.temperatureMin=2000; p.temperatureMax=2600; p.lightMin=307; p.lightMax=716; p.holdMs=2000;
  char wire[PROFILE_WIRE_SIZE]; assert(formatProfile(p,wire,sizeof(wire)));
  transfer(wire,true); assert(appliedProfile.generation==0 && changed==0);
  // Missing or out-of-order chunks cannot commit a partially changed profile.
  line("CFG:42:B:20,123"); line("CFG:42:1:bad"); line("CFG:42:E:123");
  assert(appliedProfile.generation==0);
  transfer(wire); assert(appliedProfile.generation==1 && changed==1);
  assert(!strcmp(appliedProfile.exhibit,"vase-002") && !strcmp(appliedProfile.tag,"EX-VASE"));
  assert(Serial2.output.str().find("CFG_ACK:42:E")!=std::string::npos);
  transfer(wire); assert(changed==1); // Idempotent replay does not reset timers.
  p.holdMs=3000; char conflict[PROFILE_WIRE_SIZE]; formatProfile(p,conflict,sizeof(conflict));
  transfer(conflict); assert(appliedProfile.holdMs==2000 && changed==1);
  reboot(); assert(appliedProfile.generation==1 && !strcmp(appliedProfile.exhibit,"vase-002"));
  warnings.apply(appliedProfile);
  assert(!warnings.update(2600,307,true,false,0));
  assert(!warnings.update(2600,307,true,false,1999));
  assert(warnings.update(2600,307,true,false,2000)==TEMP_HIGH);
  assert(warnings.update(2500,716,true,false,2100)==0); // Inclusive brightness endpoints, no old 22C caution.
  assert(warnings.update(2500,716,true,true,2101)==0); // No fixed swing warning under cloud profile.
  warnings.update(1999,717,true,false,2200);
  assert(warnings.update(1999,717,true,false,4200)==(TEMP_LOW|LIGHT_HIGH));
  warnings.apply(appliedProfile);
  assert(warnings.update(1999,717,true,false,4201)==0); // New profile resets hold timers.
  warnings.apply(appliedProfile);
  warnings.update(2600,500,true,false,UINT32_MAX-999);
  assert(warnings.update(2600,500,true,false,1000)==TEMP_HIGH);
  // Every possible interrupted EEPROM write retains one complete committed profile.
  auto saved=EEPROM.bytes;
  p.generation=3; strcpy(p.exhibit,"clock-003"); strcpy(p.tag,"EX-CLOCK"); strcpy(p.command,"assign-3");
  formatProfile(p,wire,sizeof(wire));
  for (int cut=0; cut<PROFILE_SLOT_SIZE+2; ++cut) {
    EEPROM.bytes=saved; EEPROM.remaining=-1; reboot(); EEPROM.remaining=cut;
    try { persistProfile(wire,p); } catch (const std::runtime_error &) {}
    EEPROM.remaining=-1; reboot();
    assert(appliedProfile.generation==1 || appliedProfile.generation==3);
    assert(!strcmp(appliedProfile.exhibit,appliedProfile.generation==1 ? "vase-002" : "clock-003"));
  }
  // The display uses the same chunked receiver, with end-to-end text integrity.
  const std::string text="Clock | New item label";
  auto displayTransfer=[&](bool corrupt) {
    Serial2.input="EPD:7:B:"+std::to_string(text.size())+","+std::to_string(profileCrc(text.c_str())^(corrupt?1:0))+"\nEPD:7:0:"+text+"\nEPD:7:E\n";
    Serial2.offset=0; checkEpaperResponse();
  };
  displayTransfer(true); assert(draws==0);
  displayTransfer(false); assert(draws==1);
}
'''
        self.compile_run(prefix + panel + assertions)

    def test_esp_profile_executor_and_label_binding(self):
        source = r'''
#include <cassert>
#include <atomic>
#include <cstring>
#include <string>
#include <vector>
#include <ctime>
#include <ArduinoJson.h>
class String {
  std::string s;
public:
  String() = default;
  String(const char *v):s(v) {}
  String(char *v):s(v) {}
  String(const std::string &v):s(v) {}
  template<class N> explicit String(N n):s(std::to_string(n)) {}
  size_t length() const { return s.size(); }
  const char *c_str() const { return s.c_str(); }
  String substring(size_t a,size_t b) const { return s.substr(a,b-a); }
  String &operator+=(const String &o) { s+=o.s; return *this; }
  friend String operator+(const String &a,const String &b) { return a.s+b.s; }
  friend bool operator!=(const String &a,const String &b) { return a.s!=b.s; }
};
size_t strlcpy(char *d,const char *s,size_t n) { size_t len=strlen(s); if(n) { size_t c=len<n-1?len:n-1; memcpy(d,s,c); d[c]=0; } return len; }
struct Log { void println(const char *) {} template<class... A> void printf(const char *,A...) {} } Serial;
uint32_t clockMs=1000;
uint32_t millis() { return clockMs; }
uint32_t esp_random() { static uint32_t n=1; return ++n; }
tm *gmtime_r(const time_t *t, tm *out) { *out = *gmtime(t); return out; }
std::atomic<uint32_t> megaProfileGeneration{0}, megaProfileSeenAt{0};
const char *SHOWCASE_ID="case-01", *DEVICE_ID="reshow-mega-01";
bool ONLINE_PROFILES_ENABLED=true, ONLINE_LABELS_ENABLED=true;
constexpr uint32_t LABEL_POLL_MS=5000;
#include "hardware_updated/Final/ESP32S3_Final/ProfileProtocol.h"
bool validIdentifier(const char *s) { return profileIdentifier(s); }
struct Preferences {
  std::vector<uint8_t> bytes;
  bool fail=false;
  bool begin(const char *,bool) { return true; }
  size_t getBytesLength(const char *) { return bytes.size(); }
  size_t getBytes(const char *,void *out,size_t n) { memcpy(out,bytes.data(),n); return n; }
  size_t putBytes(const char *,const void *data,size_t n) {
    if(fail) return 0;
    const uint8_t *p=(const uint8_t *)data; bytes.assign(p,p+n); return n;
  }
};
namespace OnlineLabel {
enum { IDLE, BUSY };
struct { int phase=IDLE; } journal;
bool ready=true;
time_t expiry=0;
time_t deadline(const char *) { return expiry; }
int result=201;
std::vector<std::string> receipts;
int request(const String &,JsonDocument &,const char *body=nullptr) { if(body) receipts.emplace_back(body); return result; }
String encodeQuery(const String &s) { return s; }
}
namespace RfidBridge {
std::atomic<uint32_t> megaBoots{1};
uint16_t transfer=0;
std::string failPart;
int sends=0, commits=0;
bool sendPart(uint16_t,const String &part,const String &,uint32_t,const char *) {
  ++sends; if(!strcmp(part.c_str(),"E")) ++commits;
  return failPart!=part.c_str();
}
bool draw(const String &,bool,uint32_t) { return true; }
}
#include "hardware_updated/Final/ESP32S3_Final/OnlineProfile.h"
void reset() {
  OnlineProfile::journal=OnlineProfile::Journal{};
  OnlineProfile::preferences=Preferences{};
  OnlineProfile::ready=false; OnlineProfile::nextPoll=0;
  RfidBridge::sends=RfidBridge::commits=0; RfidBridge::failPart="";
  OnlineLabel::result=201; OnlineLabel::expiry=time(nullptr)+120;
  OnlineLabel::receipts.clear();
  OnlineProfile::begin(); assert(OnlineProfile::ready);
}
int main() {
  JsonDocument doc;
  auto err=deserializeJson(doc,R"({"command_id":"assign-2","device_id":"reshow-mega-01","showcase_id":"case-01","type":"assignment","status":"pending","protocol":"device-fence-v1","lock_id":"showcase#case-01#assignment","resource_generation":2,"execute_before":"fixture","payload":{"exhibit_id":"vase-002","profile":{"exhibit_id":"vase-002","tag_id":"EX-VASE","version":3,"temperature_min_c":20.1,"temperature_max_c":26,"brightness_min_percent":30,"brightness_max_percent":70,"warning_seconds":2}}})");
  assert(!err);
  ExhibitProfile p;
  assert(OnlineProfile::decode(doc.as<JsonObjectConst>(),p));
  assert(p.temperatureMin==2010 && p.temperatureMax==2600 && p.lightMin==307 && p.lightMax==716 && p.holdMs==2000);
  doc["payload"]["profile"]["temperature_min_c"]=20.111;
  assert(!OnlineProfile::decode(doc.as<JsonObjectConst>(),p));
  doc["payload"]["profile"]["temperature_min_c"]=20.1;
  reset(); OnlineProfile::preferences.fail=true;
  OnlineProfile::execute(doc.as<JsonObjectConst>()); assert(RfidBridge::sends==0);
  reset(); RfidBridge::failPart="0";
  OnlineProfile::execute(doc.as<JsonObjectConst>());
  assert(RfidBridge::commits==0 && OnlineProfile::journal.active.generation==0);
  assert(OnlineProfile::journal.phase==OnlineProfile::ACK_PENDING);
  reset(); RfidBridge::failPart="E";
  OnlineProfile::execute(doc.as<JsonObjectConst>());
  assert(OnlineProfile::journal.phase==OnlineProfile::BLOCKED && OnlineProfile::journal.active.generation==0);
  reset(); OnlineProfile::execute(doc.as<JsonObjectConst>());
  assert(RfidBridge::commits==1 && OnlineProfile::journal.phase==OnlineProfile::ACK_PENDING);
  assert(!strcmp(OnlineProfile::journal.active.tag,"EX-VASE"));
  megaProfileGeneration.store(2); megaProfileSeenAt.store(clockMs);
  assert(!cloudProfileReadyForLabel("vase-002","assign-2"));
  OnlineLabel::result=503; OnlineProfile::tick();
  assert(OnlineProfile::journal.phase==OnlineProfile::ACK_PENDING);
  clockMs+=60000; OnlineLabel::result=201; OnlineProfile::tick();
  assert(OnlineLabel::receipts.size()==2 && OnlineLabel::receipts[0]==OnlineLabel::receipts[1]);
  megaProfileSeenAt.store(clockMs);
  assert(cloudProfileReadyForLabel("vase-002","assign-2"));
  assert(!cloudProfileReadyForLabel("helmet-001","assign-2"));
  assert(!cloudProfileReadyForLabel("vase-002","old-assignment"));
  megaProfileGeneration.store(1); assert(!cloudProfileReadyForLabel("vase-002","assign-2"));
  megaProfileGeneration.store(2); clockMs+=30000; assert(!cloudProfileReadyForLabel("vase-002","assign-2"));
  int sent=RfidBridge::sends; OnlineProfile::execute(doc.as<JsonObjectConst>()); assert(RfidBridge::sends==sent);
  // Restart restores active identity and does not resend a completed command.
  OnlineProfile::journal=OnlineProfile::Journal{}; OnlineProfile::begin();
  assert(OnlineProfile::journal.active.generation==2 && OnlineProfile::journal.phase==OnlineProfile::IDLE);
  OnlineProfile::journal.phase=OnlineProfile::EXECUTING; OnlineProfile::save();
  OnlineProfile::journal=OnlineProfile::Journal{}; OnlineProfile::begin();
  assert(OnlineProfile::journal.phase==OnlineProfile::BLOCKED);
  reset(); OnlineLabel::expiry=time(nullptr)+10;
  OnlineProfile::execute(doc.as<JsonObjectConst>()); assert(RfidBridge::sends==0);
}
'''
        library = ROOT / "tmp/firmware/libraries/ArduinoJson-7.4.2/src"
        if not library.is_dir():
            self.skipTest("ArduinoJson 7 headers required for executor harness")
        self.compile_run(source, [library])


if __name__ == "__main__":
    unittest.main()
