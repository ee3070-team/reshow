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

struct MonitoringLimits {
  int16_t temperatureMin;
  int16_t temperatureMax;
  uint16_t lightMin;
  uint16_t lightMax;
  uint16_t warningSeconds;
};

struct WarningTimer {
  bool running = false;
  uint32_t started = 0;
  bool update(bool condition, uint32_t now, uint32_t holdMs) {
    if (!condition) { running = false; return false; }
    if (!running) { running = true; started = now; }
    return (uint32_t)(now - started) >= holdMs;
  }
};

struct WarningState {
  WarningTimer timers[5];
  MonitoringLimits limits = {WARN_TEMP_MIN, WARN_TEMP_MAX, WARN_LIGHT_MIN, WARN_LIGHT_MAX, 5};
  bool profileActive = false;

  bool applyProfile(const MonitoringLimits &next) {
    if (next.temperatureMin < 0 || next.temperatureMax > 12500 ||
        next.temperatureMin >= next.temperatureMax || next.lightMin > next.lightMax ||
        next.lightMax > 1023 || next.warningSeconds < 1 || next.warningSeconds > 3600)
      return false;
    limits = next;
    profileActive = true;
    for (uint8_t i = 0; i < 5; ++i) timers[i] = WarningTimer();
    return true;
  }

  uint32_t holdMs() const {
    return profileActive ? (uint32_t)limits.warningSeconds * 1000UL : WARNING_HOLD_MS;
  }

  uint16_t update(int16_t temperature, uint16_t light, bool sonarKnown,
                  bool swing, uint32_t now) {
    const bool valid = temperature != -32768;
    const bool conditions[] = {
      valid && temperature < limits.temperatureMin,
      valid && temperature >= limits.temperatureMax,
      !profileActive && valid && temperature >= WARN_TEMP_CAUTION && temperature < WARN_TEMP_MAX,
      light < limits.lightMin, light > limits.lightMax
    };
    uint16_t mask = (valid ? 0 : TEMP_FAULT) | (sonarKnown ? 0 : SONAR_FAULT);
    for (uint8_t i = 0; i < 5; ++i)
      if (timers[i].update(conditions[i], now, holdMs())) mask |= (1U << i);
    if (!profileActive && swing) mask |= TEMP_SWING;
    return mask;
  }
};
