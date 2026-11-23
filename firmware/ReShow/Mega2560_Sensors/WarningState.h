#pragma once
#include <stdint.h>

// Bench defaults from the original sensor sketch. Adjust for the installed exhibit.
// Temperature is centi-Celsius; brightness is the Mega's raw 10-bit ADC, NOT lux.
constexpr int16_t WARN_TEMP_MIN = 1800;
constexpr int16_t WARN_TEMP_CAUTION = 2200;
constexpr int16_t WARN_TEMP_MAX = 2400;  // Exclusive upper safe bound.
constexpr uint16_t WARN_LIGHT_MIN = 300;
constexpr uint16_t WARN_LIGHT_MAX = 600;
constexpr uint32_t WARNING_HOLD_MS = 5000UL;

enum WarningBit : uint16_t {
  TEMP_LOW = 1, TEMP_HIGH = 2, TEMP_CAUTION = 4,
  LIGHT_LOW = 8, LIGHT_HIGH = 16, TEMP_SWING = 32,
  TEMP_FAULT = 64, SONAR_FAULT = 128
};

struct WarningTimer {
  bool running = false;
  uint32_t started = 0;
  bool update(bool condition, uint32_t now) {
    if (!condition) { running = false; return false; }
    if (!running) { running = true; started = now; }
    return (uint32_t)(now - started) >= WARNING_HOLD_MS;
  }
};

struct WarningState {
  WarningTimer timers[5];
  uint16_t update(int16_t temperature, uint16_t light, bool sonarKnown,
                  bool swing, uint32_t now) {
    const bool valid = temperature != -32768;
    const bool conditions[] = {
      valid && temperature < WARN_TEMP_MIN,
      valid && temperature >= WARN_TEMP_MAX,
      valid && temperature >= WARN_TEMP_CAUTION && temperature < WARN_TEMP_MAX,
      light < WARN_LIGHT_MIN, light > WARN_LIGHT_MAX
    };
    uint16_t mask = (valid ? 0 : TEMP_FAULT) | (sonarKnown ? 0 : SONAR_FAULT);
    for (uint8_t i = 0; i < 5; ++i)
      if (timers[i].update(conditions[i], now)) mask |= (1U << i);
    if (swing) mask |= TEMP_SWING;
    return mask;
  }
};
