// All allowlisted RFID cards for hardware_updated_RFID_lock.ino.
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
  { "04833912EE1B90", "door" },  // student card 59702090
  { "2CF24018", ""},         // blue tage cs-344
  { "D04F111B", "epaper"},         // white card cs-522
  { "2345339E", "wifi"},         // 201b card --> test if can do wifi ; post to https://httpbin.org/
   
  // { "A1B2C3D4", "edit" },     // staff edit card
};

const uint8_t rfidCardCount = sizeof(rfidCards) / sizeof(rfidCards[0]);
