/*
  Wi-Fi-only diagnostic for ESP32-S3, Espressif Arduino core 3.x.
  Board: ESP32S3 Dev Module. Serial Monitor: 115200 baud.
  Fill in ONLY your hotspot SSID/password. No AWS token or sensors needed.
  This temporarily replaces the AWS sketch on the ESP32; keep your configured
  AWS sketch saved so you can upload it again after testing.
  One connection attempt per reset: no overlapping reconnect calls.
*/
#include <Arduino.h>
#include <WiFi.h>

#if __has_include("local_config.h")
#include "local_config.h"
#else
#include "local_config.example.h"
#endif


uint32_t attemptStarted = 0;
bool attempted = false;

void wifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  // These callbacks run in a different task; only log event data here.
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      Serial.println("ASSOCIATED: joined hotspot; waiting for DHCP/IP address.");
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.println("GOT_IP: Wi-Fi connection and DHCP succeeded.");
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      Serial.printf("DISCONNECTED: reason=%u (report this number).\n",
                    (unsigned int)info.wifi_sta_disconnected.reason);
      break;
    case ARDUINO_EVENT_WIFI_STA_LOST_IP:
      Serial.println("LOST_IP: IP address is no longer valid.");
      break;
    default:
      break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("\nESP32-S3 Wi-Fi diagnostic; no AWS requests.");
  if (strncmp(WIFI_SSID, "REPLACE_", 8) == 0 ||
      strncmp(WIFI_PASSWORD, "REPLACE_", 8) == 0) {
    Serial.println("Edit the two Wi-Fi constants, upload, then press RESET.");
    return;
  }
  WiFi.onEvent(wifiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  Serial.printf("Looking for exact SSID: [%s]\n", WIFI_SSID);
  int networks = WiFi.scanNetworks(false, true);  // Include hidden APs in the diagnostic list.
  bool found = false;
  if (networks < 0) Serial.printf("Scan failed, result=%d\n", networks);
  else Serial.printf("SCAN COMPLETE: %d nearby networks. Compare the names below with your hotspot.\n", networks);
  for (int i = 0; i < networks; ++i) {
    String scannedName = WiFi.SSID(i);
    Serial.printf("  SSID=[%s] channel=%ld RSSI=%ld dBm security_code=%d\n",
      scannedName.length() ? scannedName.c_str() : "<hidden>",
      (long)WiFi.channel(i), (long)WiFi.RSSI(i), (int)WiFi.encryptionType(i));
    if (WiFi.SSID(i) == WIFI_SSID) {
      found = true;
      Serial.printf("TARGET FOUND: channel=%ld RSSI=%ld dBm security_code=%d\n",
        (long)WiFi.channel(i), (long)WiFi.RSSI(i), (int)WiFi.encryptionType(i));
    }
  }
  if (!found) {
    Serial.println("TARGET NOT FOUND: check exact name, visible SSID, 2.4 GHz and signal.");
    Serial.println("Still trying a direct connection (a hidden hotspot may not appear in scan).");
  }
  WiFi.scanDelete();
  Serial.println("Starting ONE connection attempt; password is not printed.");
  attemptStarted = millis();
  attempted = true;
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  static uint32_t lastLog = 0;
  static bool successPrinted = false;
  static bool timeoutPrinted = false;
  if (!attempted) { delay(100); return; }
  uint32_t now = millis();
  if (WiFi.status() == WL_CONNECTED) {
    if (!successPrinted) {
      successPrinted = true;
      Serial.print("SUCCESS: IP="); Serial.print(WiFi.localIP());
      Serial.print(" gateway="); Serial.print(WiFi.gatewayIP());
      Serial.printf(" RSSI=%ld dBm\n", (long)WiFi.RSSI());
      Serial.println("Hotspot connection works. This does not yet test internet, NTP or AWS.");
    }
  } else {
    successPrinted = false;
    if ((uint32_t)(now - lastLog) >= 5000) {
      lastLog = now;
      Serial.printf("Waiting: WiFi.status=%d elapsed=%lu s\n",
        (int)WiFi.status(), (unsigned long)((now - attemptStarted) / 1000));
    }
    if (!timeoutPrinted && (uint32_t)(now - attemptStarted) >= 60000) {
      timeoutPrinted = true;
      Serial.println("No IP after 60 s. Report scan output and DISCONNECTED reason lines.");
      Serial.println("After changing hotspot settings, press RESET for a fresh test.");
    }
  }
  delay(20);
}
