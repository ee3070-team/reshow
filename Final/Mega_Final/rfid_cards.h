// Fixed RFID roles for the merged Mega sketch.
//
// role "door" unlocks the relay and starts the exhibit edit workflow.
// role "edit" never touches the relay; it replaces the browser's
// "Mark manual update finished" button and finishes a pending edit.
// role "wifi" sends an anonymous test message to httpbin via the ESP32.
// role "epaper" starts an edit without unlocking and displays server status.
// An empty role is treated as an invalid card.
//
// Add one line per card. Copy the UID from the serial monitor (115200 baud):
// every scan prints the card's UID even when it is denied.
//
// Cards learned at runtime with the serial commands 'L' (door) and 'M' (edit)
// are saved in EEPROM and do not need to be listed here.
#pragma once

struct RfidCardEntry {
  const char *uid;
  const char *role;
};

const RfidCardEntry rfidCards[] = {
  { "04833912EE1B90", "door" },   // Door card
  { "2CF24018", "" },             // Blue tag, denied
  { "D04F111B", "epaper" },       // White e-paper card
  { "2345339E", "wifi" },         // Anonymous connectivity test
   
  // { "A1B2C3D4", "edit" },     // staff edit card
};

const uint8_t rfidCardCount = sizeof(rfidCards) / sizeof(rfidCards[0]);
