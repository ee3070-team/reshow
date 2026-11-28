#pragma once

// Enable once the backend grants command and receipt access for this device.
constexpr bool ONLINE_LABELS_ENABLED = true;
constexpr bool ONLINE_PROFILES_ENABLED = true;
// Offline wiring test: replaces the panel image once at boot, never sends an ACK.
// Use only on the bench with ONLINE_LABELS_ENABLED=false. Restore false afterwards.
constexpr bool EPAPER_BENCH_TEST = false;
// Define RESHOW_SHOWCASE_ID in ignored local_config.h using the registered showcase ID.
#ifndef RESHOW_SHOWCASE_ID
#define RESHOW_SHOWCASE_ID "REPLACE_SHOWCASE_ID"
#endif
const char SHOWCASE_ID[] = RESHOW_SHOWCASE_ID;

// GDEH0154D67, 1.54-inch 200x200 B/W, Waveshare SPI driver board.
// ESP32-S3 GPIO numbers (not board silkscreen Dx aliases). UART stays on 18/17.
constexpr int EPD_SCK = 12;
constexpr int EPD_MOSI = 11;
constexpr int EPD_CS = 10;
constexpr int EPD_DC = 9;
constexpr int EPD_RST = 8;
constexpr int EPD_BUSY = 7;
constexpr uint32_t LABEL_POLL_MS = 5000;
constexpr uint32_t LABEL_MIN_REFRESH_MS = 180000;  // 3 minutes, full refresh only.

// Set the LAN server origin in local_config.h, e.g. http://192.168.1.10:8000.
// Empty keeps RFID scans local. No AWS bearer token is sent to the LAN service.
#ifndef RFID_SERVER_ORIGIN
#define RFID_SERVER_ORIGIN ""
#endif
// The lock prototype has a Mega-mounted panel. Set false for an ESP-mounted panel.
constexpr bool RFID_PANEL_ON_MEGA = true;
// Optional public echo test. Only a fixed anonymous greeting is sent on a Wi-Fi card tap.
constexpr bool WIFI_CARD_ECHO_ENABLED = false;
