#pragma once
#include <math.h>
#include <stdint.h>

struct AssignmentLimits {
  int16_t temperatureMin;
  int16_t temperatureMax;
  uint16_t lightMin;
  uint16_t lightMax;
  uint16_t warningSeconds;
};

inline bool assignmentLimits(double temperatureMin, double temperatureMax,
                             double brightnessMin, double brightnessMax,
                             uint32_t warningSeconds, AssignmentLimits &result) {
  if (!isfinite(temperatureMin) || !isfinite(temperatureMax) ||
      !isfinite(brightnessMin) || !isfinite(brightnessMax) ||
      temperatureMin < 0 || temperatureMax > 125 || temperatureMin >= temperatureMax ||
      brightnessMin < 0 || brightnessMax > 100 || brightnessMin >= brightnessMax ||
      warningSeconds < 1 || warningSeconds > 3600) return false;

  double minCenti = temperatureMin * 100;
  double maxCenti = temperatureMax * 100;
  double roundedMin = round(minCenti), roundedMax = round(maxCenti);
  if (temperatureMin != roundedMin / 100 || temperatureMax != roundedMax / 100)
    return false;

  double minAdc = brightnessMin * 1023 / 100;
  double maxAdc = brightnessMax * 1023 / 100;
  // Decimal input very close to an interior ADC boundary cannot be safely
  // rounded after conversion to binary floating point.
  if ((brightnessMin > 0 && brightnessMin < 100 && fabs(minAdc - round(minAdc)) < 1e-9) ||
      (brightnessMax > 0 && brightnessMax < 100 && fabs(maxAdc - round(maxAdc)) < 1e-9))
    return false;
  uint16_t low = (uint16_t)ceil(minAdc), high = (uint16_t)floor(maxAdc);
  if (low > high) return false;
  result = {(int16_t)roundedMin, (int16_t)roundedMax, low, high, (uint16_t)warningSeconds};
  return true;
}
