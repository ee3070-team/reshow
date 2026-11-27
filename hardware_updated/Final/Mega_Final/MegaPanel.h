#pragma once
#include <GxEPD2_BW.h>

const uint8_t epaperCsPin = 43;
const uint8_t epaperDcPin = 41;
const uint8_t epaperRstPin = 39;
const uint8_t epaperBusyPin = 37;
GxEPD2_BW<GxEPD2_154_D67, 40> epaper(
  GxEPD2_154_D67(epaperCsPin, epaperDcPin, epaperRstPin, epaperBusyPin));

bool sawPanelBusy = false;
void notePanelBusy(const void *) { sawPanelBusy = true; }

bool drawMegaPanel(const char *text) {
  // No refresh starts while the relay is unlocked. BUSY is active high on D67.
  if (doorUnlocked) return false;
  sawPanelBusy = false;
  uint32_t started = millis();
  epaper.epd2.setBusyCallback(notePanelBusy);
  epaper.init(0, true, 2, false);
  epaper.setFullWindow();
  epaper.firstPage();
  do {
    epaper.fillScreen(GxEPD_WHITE);
    epaper.setTextColor(GxEPD_BLACK);
    epaper.setTextSize(1);
    epaper.setTextWrap(true);
    epaper.setCursor(8, 12);
    epaper.print(text);
  } while (epaper.nextPage());
  bool completed = sawPanelBusy && digitalRead(epaperBusyPin) == LOW;
  epaper.hibernate();
  return completed && millis() - started < 9000UL;
}

void checkEpaperResponse() {
  static char line[64];
  static uint8_t length = 0;
  static bool overflow = false;
  static char description[181];
  static uint16_t transferId = 0, expectedCrc = 0;
  static bool checkCrc = false;
  static uint8_t expectedLength = 0;
  static uint8_t receivedLength = 0;
  static uint8_t nextChunk = 0;
  static bool receiving = false;
  while (Serial2.available()) {
    char c = Serial2.read();
    if (c == '\n') {
      line[length] = '\0';
      if (!overflow && handleProfileLine(line)) {
        // Profile transfer shares the single Serial2 reader.
      } else if (!overflow && strncmp(line, "EPD:", 4) == 0) {
        const char *phase = line + 4;
        if (strcmp(phase, "idle") == 0 || strcmp(phase, "pending_edit") == 0 ||
            strcmp(phase, "ready_to_confirm") == 0) {
          Serial.print("Server RFID status: ");
          Serial.println(phase);
          drawMegaPanel(phase);
        } else {
          char *idEnd;
          unsigned long id = strtoul(line + 4, &idEnd, 10);
          if (idEnd == line + 4 || *idEnd != ':' || id == 0 || id > 65535) {
            Serial.println("Invalid e-paper message from ESP32.");
          } else {
            char *command = idEnd + 1;
            char *separator = strchr(command, ':');
            if (separator) *separator++ = '\0';
            bool accepted = false;
            if (strcmp(command, "B") == 0 && separator) {
              char *end;
              unsigned long size = strtoul(separator, &end, 10);
              checkCrc = *end == ',';
              if (checkCrc) {
                uint32_t value;
                if (!profileNumber(end + 1, 65535, value)) { receiving = false; continue; }
                expectedCrc = value; end += strlen(end);
              }
              receiving = *separator && !*end && size >= 1 && size <= 180;
              if (receiving) {
                transferId = (uint16_t)id;
                expectedLength = (uint8_t)size;
                receivedLength = 0;
                nextChunk = 0;
                accepted = true;
              }
            } else if (receiving && id == transferId && strcmp(command, "E") == 0 && !separator) {
              if (receivedLength == expectedLength) {
                description[receivedLength] = '\0';
                accepted = (!checkCrc || profileCrc(description) == expectedCrc) && drawMegaPanel(description);
                receiving = false;
                Serial.println(accepted ? F("E-paper updated.") : F("E-paper BUSY check failed."));
              }
            } else if (receiving && id == transferId && separator) {
              char *end;
              unsigned long chunk = strtoul(command, &end, 10);
              size_t chunkLength = strlen(separator);
              if (*command && !*end && chunk == nextChunk && chunkLength > 0 &&
                  chunkLength <= 24 && receivedLength + chunkLength <= expectedLength) {
                memcpy(description + receivedLength, separator, chunkLength);
                receivedLength += chunkLength;
                nextChunk++;
                accepted = true;
              }
            }
            Serial2.print(accepted ? "EPD_ACK:" : "EPD_ERR:");
            Serial2.print(id);
            Serial2.print(':');
            Serial2.println(command);
          }
        }
      } else if (overflow) {
        Serial.println("Oversized message from ESP32.");
      }
      length = 0;
      overflow = false;
    } else if (c != '\r' && !overflow) {
      if (length < sizeof(line) - 1) line[length++] = c;
      else overflow = true;
    }
  }
}
