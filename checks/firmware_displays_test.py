"""Run actual warning/layout firmware logic on the host; no network or boards."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from checks.firmware_test import function

ROOT = Path(__file__).resolve().parents[1]


class DisplayFirmwareTests(unittest.TestCase):
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
