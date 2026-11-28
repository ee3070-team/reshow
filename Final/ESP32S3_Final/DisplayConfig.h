#pragma once

// Enable once the backend grants command and receipt access for this device.
constexpr bool ONLINE_LABELS_ENABLED = true;
// Enable only after both boards have the matching firmware and cloud permissions.
constexpr bool ONLINE_ASSIGNMENTS_ENABLED = true;
// Enable only after verifying recovery on isolated hardware and backend data.
constexpr bool ONLINE_ASSIGNMENT_RECOVERY_ENABLED = true;
// Offline wiring test: replaces the panel image once at boot, never sends an ACK.
// Use only on the bench with ONLINE_LABELS_ENABLED=false. Restore false afterwards.
constexpr bool EPAPER_BENCH_TEST = false;
const char SHOWCASE_ID[] = "showcase-reshow-mega-01";

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

// RFID tap events are cloud-only; the legacy LAN workflow is disabled.
constexpr bool LOCAL_RFID_ENABLED = false;
#ifndef RFID_SERVER_ORIGIN
#define RFID_SERVER_ORIGIN ""
#endif
// The lock prototype has a Mega-mounted panel. Set false for an ESP-mounted panel.
constexpr bool RFID_PANEL_ON_MEGA = true;
// Retained for the legacy bridge; Wi-Fi test cards are not forwarded.
constexpr bool WIFI_CARD_ECHO_ENABLED = false;
// Upload RFID taps to the cloud using the hardware producer token.
constexpr bool CLOUD_RFID_ENABLED = true;
