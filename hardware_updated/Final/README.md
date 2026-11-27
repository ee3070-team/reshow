# ReShow two-board bench firmware

Open `Mega_Final/Mega_Final.ino` for the Arduino Mega 2560 and `ESP32S3_Final/ESP32S3_Final.ino` for the ESP32-S3. Extract the whole ZIP first. Arduino needs the sibling headers in each sketch folder. Without configured Wi-Fi credentials, both sketches operate in offline bench mode. With credentials, the ESP joins Wi-Fi independently of the AWS upload flag.

The merge uses the five sketches under [hardware_updated at 8927fef](https://github.com/ee3070-team/reshow/tree/8927fef4f8325418b7973ef6d7b44fe3c5184822/hardware_updated). It carries forward the sensor and RFID prototypes, the lock sketch's card roles and EEPROM format, the checksummed telemetry pair, and the online label executor. Sensor readings use the 10-bit ADC and direct LM35 conversion. The Mega sends one V2 sensor snapshot with its applied exhibit ID and profile generation every ten seconds and sends card events immediately on the same UART.

## What is merged

| Source | Final behavior |
| --- | --- |
| `hardware_sensor_original` and `Mega2560_Sensors` | LM35, light ADC, proximity/engagement, automatic and manual lighting, OLED warnings, CRC-protected telemetry |
| `hardware_updated_RFID` and `hardware_updated_RFID_lock` | RC522 card roles, timed relay, EEPROM learning, green card feedback, Wi-Fi test request and Mega e-paper |
| `ESP32S3_AWS` | UART reception during network requests, bounded telemetry queue, authenticated HTTPS uploads and optional AWS labels |
| Integration in `RfidBridge.h` | Route RFID JSON and sensor frames on the same UART, send taps to the local server, transfer display text with per-chunk acknowledgements |

Upload only these two final sketches. The historical sketches are alternatives, not extra Arduino tabs. The original `LM35IC.h` dependency is replaced by direct calibrated ADC conversion; install the libraries listed below. The latest lock sketch's RGB pin layout is the default.

RFID events go to the repository's local `server.py`; AWS receives sensor/status records. Online assignment commands now persist and apply the exhibit ID, tag and monitoring limits on Mega. AWS staff-card synchronization, remote unlock, remote lighting and a latch-position sensor are not implemented. Cloud latch status remains unknown (`null`). AWS labels support the Mega-mounted panel and the optional ESP-mounted panel; LAN RFID labels support either panel location when online labels do not own it. UID matching retains the prototype's access behavior and is not cryptographic card authentication.

## Install and upload

| Board in Arduino IDE | Board package used | Library Manager dependencies |
| --- | --- | --- |
| Arduino Mega or Mega 2560, processor ATmega2560 | Arduino AVR Boards 1.8.8 | MFRC522 1.4.12, GxEPD2 1.6.4, Adafruit SSD1306 2.5.17, Adafruit GFX Library and Adafruit BusIO |
| ESP32S3 Dev Module | esp32 by Espressif Systems 3.3.12 | ArduinoJson 7.4.2, GxEPD2 1.6.4, Adafruit GFX Library and Adafruit BusIO |

Select the matching USB port for each upload. Both USB Serial Monitors use **115200 baud**. For boards with native ESP USB, enable USB CDC on boot if the monitor uses that port. The board-to-board UART runs at **9600 8N1**.

## Wire the prototype

| Signal | Connection |
| --- | --- |
| Mega → ESP | Mega **D16 / TX2** through a **5 V to 3.3 V level shifter or divider** to ESP **GPIO18 / RX** |
| ESP → Mega | ESP **GPIO17 / TX** to Mega **D17 / RX2** for Mega-mounted e-paper |
| Reference | Common GND between both boards and peripherals |
| LM35 and light sensor | Mega A1 and A0 respectively |
| Ultrasonic sensor | Mega D3 trigger, D2 echo |
| Two RGB LEDs in R/G/B order | Mega **40/42/38** and **36/34/32**, active high. If your harness follows the original sensor sketch, set `LED_PINS` to `{13,12,11,48,46,44}` in Mega_Final.ino. Set `LED_ACTIVE_HIGH=false` for common-anode wiring. |
| RC522 | Mega D53 SS/SDA, D49 RST, D51 MOSI, D50 MISO, D52 SCK. Use 3.3 V power and level-shift Mega outputs for a 3.3 V reader. |
| Door relay module | Mega D47 IN, active low by default. Match `doorRelayActiveLow` in RfidAccess.h to the module. Test the relay indicator with the lock load disconnected first. |
| SSD1306 OLED, 128×64 I²C | Mega D20 SDA, D21 SCL. Addresses 0x3C and 0x3D are probed. Use power and I²C levels rated for your breakout. |
| Mega-mounted e-paper, RFID-lock wiring | Mega D51 MOSI, D52 SCK, D43 CS, D41 DC, D39 RST, D37 BUSY. RC522 shares SPI and has its own CS. |
| ESP-mounted e-paper, alternative wiring | ESP GPIO11 MOSI, GPIO12 SCK, GPIO10 CS, GPIO9 DC, GPIO8 RST, GPIO7 BUSY. |

Use the GDEH0154D67 1.54-inch 200×200 B/W panel and its SPI driver board. Use 3.3 V logic or a level-shifting breakout for Mega outputs. Connect a panel to **one** board. The default LAN RFID workflow addresses a Mega-mounted panel. For an ESP-mounted panel, set `RFID_PANEL_ON_MEGA=false` in DisplayConfig.h. AWS label commands use the same selected panel. With online AWS labels enabled and a showcase ID configured, they own that panel and LAN taps still reach the RFID server.

Any active OLED warning, including sensor faults, turns both RGB LEDs solid red (red on, green/blue off). Environmental warnings respect the applied warning delay. Red takes priority over Auto/On/Off and RFID green feedback, even when the OLED itself is unavailable. When all warnings clear, the selected lighting mode resumes, or any unexpired RFID feedback finishes. The selected mode is not changed by a warning.

## First test without Wi-Fi

1. Upload both sketches. Open both monitors. Within about ten seconds the Mega should print `To ESP32: V2,...` and the ESP should print `Mega seq=...`. Compare the sequence numbers. With no Wi-Fi credentials the ESP prints `WIFI NOT CONFIGURED`; UART reception continues.
2. Cover the light sensor and move an object inside 15 cm of the ultrasonic sensor. Check the OLED and the next ESP reading. Auto lighting clears after a second of valid far readings. Send `O`, `F`, and `A` to the **Mega USB** monitor for on, off, and auto.
3. Tap the configured door card. Expect an accepted door event on both monitors and a roughly three-second relay pulse. Tap an unregistered card and expect a denied event with the relay staying locked. The white e-paper card produces a green flash. The Wi-Fi card sends an anonymous diagnostic frame. Card mappings are in rfid_cards.h.
4. To exercise EEPROM learning, scan a spare unlisted card and send `M` for an edit card or `L` for a door card on Mega USB. Reset, rescan, and check the role. `D` lists roles. `X` clears all learned cards. Cards with a fixed role, including the explicitly denied blue tag, are configured in rfid_cards.h.
5. For a Mega-mounted panel, send `P` to the Mega USB monitor. Expect the bench text and `Panel OK`. For an ESP-mounted panel, set `EPAPER_BENCH_TEST=true` with `ONLINE_LABELS_ENABLED=false`, upload, and inspect the printed BUSY result and the image. Restore the flag after the test. Both tests replace the current panel image. Space full refreshes at least three minutes apart.

## Connect the local RFID workflow

Start the repository's server on your bench computer with `python server.py --bind 0.0.0.0 --port 8000`. Connect the ESP and computer to a network that permits traffic between them. In the ESP sketch folder copy `local_config.example.h` to `local_config.h`. Enter the Wi-Fi credentials and set `RFID_SERVER_ORIGIN` to the computer's LAN origin, for example `"http://192.168.1.10:8000"` without a trailing slash. Re-upload the ESP. AWS can stay disabled.

A door or e-paper card starts the local exhibit edit workflow. An edit card can finish it. Check `RFID POST -> 200` on ESP USB and the local site's RFID workflow. Save a short printable ASCII description (up to 180 characters). The ESP polls about every ten seconds, refreshes the selected panel, and posts a delivery receipt after driver confirmation. Changed images observe a three-minute cooldown. The Mega returns a numbered ACK per chunk and an end ACK after the BUSY check. If only the forward UART wire works, telemetry and taps arrive but a Mega display transfer times out.

The ESP queues up to 12 taps in RAM while disconnected from a configured network. An unconfirmed HTTP tap is logged for manual checking because the local endpoint has no idempotency key. To enable the original Wi-Fi diagnostic card's public HTTP echo, set `WIFI_CARD_ECHO_ENABLED=true` in DisplayConfig.h and configure Wi-Fi. Only a fixed greeting goes to the echo service.

## Optional AWS tests

Set the device token in local_config.h. Confirm `DEVICE_ID` and `EXHIBIT_ID` in ESP32S3_Final.ino with the backend owner, then enable `HARDWARE_UPLOADS_ENABLED`. The factory/legacy default exhibit is `helmet-001`. After an online assignment, Mega sends the applied exhibit ID with every sensor snapshot and accepted RFID tap. ESP preserves that captured ID in queued readings and taps, even if a different item is applied before upload. Wait for Wi-Fi and NTP. Expect HTTP 200/201 for device status and for readings with valid temperature and occupancy.

## Online item switching (27 November 2026)

1. Add `#define RESHOW_SHOWCASE_ID "your-registered-showcase-id"` to the ignored ESP `local_config.h`, alongside the existing Wi-Fi credentials and hardware bearer token. Use the showcase ID from the backend, not `reshow-mega-01` (the device ID). Both online command gates are enabled in `DisplayConfig.h`; the placeholder showcase ID prevents execution until configured. A valid hardware token is required. `HARDWARE_UPLOADS_ENABLED` controls telemetry only; display fetching works with it disabled. No credentials are included in the source bundle.
2. Upload **both** `Mega_Final.ino` and `ESP32S3_Final.ino`, with all sibling headers. The return wire ESP GPIO17/TX to Mega D17/RX2 is required for profile and Mega-label commands. Keep `RFID_PANEL_ON_MEGA=true` for the current wiring.
3. In the website Live workspace, select the exhibit, save its tag and monitoring limits, then choose **Apply profile**. For the first use of this firmware, reapply the profile even if the backend already lists it as assigned. Wait for device acknowledgement. Selecting a row or saving a profile alone does not change hardware.
4. Mega validates the complete checksummed profile, writes a second EEPROM slot, commits it, applies the limits and tag, and only then acknowledges ESP. The ESP persists the receipt before sending it to the backend. Temperature is stored in hundredths of a degree; brightness percentages become `ceil(min * 1023/100)` and `floor(max * 1023/100)`. The maximum temperature triggers a high warning; both brightness endpoints are permitted. The profile's warning delay replaces the bench delay. Cloud profiles disable the old fixed 22-degree caution and 2-degree daily swing warnings, matching the backend monitoring contract. Sensor fault warnings remain.
5. Open **Electronic label**, review the selected item's draft, and choose **Send to showcase**. A profile change does not automatically replace the physical label. The website marks a retained label that belongs to another item. New Mega drafts start without an image; existing drafts are preserved. The Mega label accepts up to 180 printable ASCII characters for `title | collection | description`, including separators, with no image or line breaks. The tag remains the item's identity/mapping; the displayed label text is the content you send.
6. ESP checks the label's exhibit ID **and exact assignment command ID**, plus a recent matching Mega profile generation, before refreshing. A label for a previous assignment cannot overwrite the current item. The Mega returns completion only after its display BUSY check. The three-minute refresh cooldown also applies after restart, so allow time for the label to update.

Profile EEPROM occupies two slots beginning at address 512, separate from learned RFID cards. The active profile survives Mega resets. ESP assignment and label journals persist in separate NVS namespaces. A missing commit acknowledgement, reset during execution, rejected receipt or uncertain display result leaves the operation fenced and blocked; it is never reported as successful. Automated recovery acknowledgements are not implemented: retain both journals and reconcile the backend/device outcome before resuming. Do not erase EEPROM/NVS merely to clear a pending command.

### Display data fetching

The ESP calls `fetchWarningData()` and `fetchExhibitData()` after Wi-Fi and UTC synchronization, independently of sensor uploads, upload retries and whether any sensor sample has arrived. Both use authenticated HTTPS at `https://urauw4ckw1.execute-api.us-east-1.amazonaws.com` with certificate verification.

| Function | Backend reads | Mega behavior |
| --- | --- | --- |
| `fetchWarningData()` | `GET /api/commands?device_id=<device>&type=assignment&status=pending&limit=1`, then `GET /api/commands/{command_id}` | Persist and apply the frozen temperature/light limits and warning delay over the checksummed `CFG` transfer. OLED evaluates live sensors against these limits. When no fault is active, it shows the applied limits and delay; active faults take priority. |
| `fetchExhibitData()` | Same command routes with `type=label` | Transfer the frozen title, collection and description over `EPD`, refresh the e-paper and report completion after its BUSY check. |

Polling is scheduled every five seconds, with a one-minute retry after failed reads. Label polling waits for the three-minute display refresh cooldown. The fetched label must match the exact applied assignment. Empty polls leave both displays and their existing settings intact. Warning data means monitoring limits and delay, not historical `/api/warnings` records. The hardware credential cannot read that historical collection. The website's **Apply profile** and **Send to showcase** actions create the respective commands; saving an exhibit or draft alone does not send one.

At boot, check `DISPLAY FETCH | backend=ready | warning limits=enabled | exhibit label=enabled` on ESP USB. Mega prints the applied temperature/light limits and delay when a profile is restored or received. `OK / fetched limits` distinguishes a downloaded profile from `OK / bench limits` on the OLED. The light range is shown in the actual 0–1023 ADC units used by the Mega.

Read-only live verification on 27 November confirmed the local device `reshow-mega-01` is bound to `showcase-reshow-mega-01` in its pending assignment command. That showcase ID is configured in this workspace's private `local_config.h`. The API accepted assignment and label polling and the exact assignment read; there was no pending label at verification time. Other installations must supply their own IDs and credentials.

The repository backend already authorizes scoped assignment/label polling and acknowledgement for hardware credentials. The deployed backend must include that implementation and the correct device/showcase registration. This source update has not provisioned cloud records or flashed physical boards. First bench verification should use two profiles with distinct tags, temperature/brightness limits and warning delays; apply each, observe the OLED, inspect uploaded reading IDs, send its label, then reboot both boards and confirm the last profile remains active.

## Wi-Fi v2 troubleshooting

The ESP reads `local_config.h` when present, otherwise `local_config.example.h`. Keep real credentials in the ignored local file. After editing, upload `ESP32S3_Final.ino` again; changing a file on the PC does not change firmware already on the board.

Open the 115200-baud Serial Monitor and press RESET. After about two seconds expect `ReShow WiFi v2 | config=local_config.h | WiFi credentials=loaded`. The same banner reports the AWS flag and configuration status without printing the password or token. If you still see the old `UPLOADS DISABLED: provision a hardware producer...` line, that message belongs to the previous firmware; check the sketch folder, selected board/port, and successful upload result. ROM boot messages alone do not prove this application is running. If your working sketch uses native USB CDC, retain the same USB CDC on boot and port settings for this sketch.

`WIFI ASSOCIATED` means the hotspot was joined; `WIFI GOT_IP` and `WIFI CONNECTED: IP=...` confirm DHCP succeeded. `WIFI DISCONNECTED: reason=...` provides the driver reason for diagnosis. Wi-Fi startup now follows the working `WIFI.ino`: station mode, automatic reconnect, and `WiFi.begin` with the saved credentials. The main loop reports status instead of forcing a reconnect every 15 seconds. AWS credentials/IDs and NTP are checked separately after Wi-Fi connects. See the [Espressif Wi-Fi API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html) for the underlying connection and event APIs.
