#pragma once

// Separate gate: telemetry credentials currently do NOT authorize commands.
// Enable only once the backend grants this hardware device the routes in DISPLAYS.md.
constexpr bool ONLINE_LABELS_ENABLED = false;
// Offline wiring test: replaces the panel image once at boot, never sends an ACK.
// Use only on the bench with ONLINE_LABELS_ENABLED=false. Restore false afterwards.
constexpr bool EPAPER_BENCH_TEST = false;
const char SHOWCASE_ID[] = "REPLACE_SHOWCASE_ID";

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
