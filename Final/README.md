# ReShow two-board bench firmware

Open `Mega_Final/Mega_Final.ino` for the Arduino Mega 2560 and `ESP32S3_Final/ESP32S3_Final.ino` for the ESP32-S3. Extract the whole ZIP first. Arduino needs the sibling headers in each sketch folder. Without configured Wi-Fi credentials, both sketches operate in offline bench mode. With credentials, the ESP joins Wi-Fi independently of the AWS upload flag.

The merge uses the five sketches under [hardware_updated at 8927fef](https://github.com/ee3070-team/reshow/tree/8927fef4f8325418b7973ef6d7b44fe3c5184822/hardware_updated). It carries forward the sensor and RFID prototypes, the lock sketch's card roles and EEPROM format, the checksummed telemetry pair, and the online label executor. Sensor readings use the 10-bit ADC and direct LM35 conversion. The Mega sends one V1 sensor snapshot every ten seconds and sends card events immediately on the same UART.

## What is merged

| Source | Final behavior |
| --- | --- |
| `hardware_sensor_original` and `Mega2560_Sensors` | LM35, light ADC, proximity/engagement, automatic and manual lighting, OLED warnings, CRC-protected telemetry |
| `hardware_updated_RFID` and `hardware_updated_RFID_lock` | RC522 card roles, timed relay, EEPROM learning, green card feedback, Wi-Fi test request and Mega e-paper |
| `ESP32S3_AWS` | UART reception during network requests, bounded telemetry queue, authenticated HTTPS uploads and optional AWS labels |
| Integration in `RfidBridge.h` | Route RFID JSON and sensor frames on the same UART and upload taps directly to AWS |

Upload only these two final sketches. The historical sketches are alternatives, not extra Arduino tabs. The original `LM35IC.h` dependency is replaced by direct calibrated ADC conversion; install the libraries listed below. The latest lock sketch's RGB pin layout is the default.

RFID tap events are sent to AWS only; the local `server.py` RFID workflow is disabled. AWS also receives sensor/status records. Cloud assignment and label execution are separate opt-in workflows and require the matching firmware on both boards. The merge does not add AWS staff-card synchronization, remote unlock, remote lighting, or a latch-position sensor. Cloud latch status remains unknown (`null`). Cloud label commands can target a Mega- or ESP-mounted panel. UID matching retains the prototype's access behavior and is not cryptographic card authentication.

The Mega can validate, persist and restore a profile received over its serial
link. When enabled, the ESP32 polls the existing cloud assignment endpoints,
durably fences the command and sends a success receipt only after the Mega
confirms the saved, active profile. Values too close to an ADC boundary for
reliable floating-point conversion are rejected rather than rounded
ambiguously. This path still requires physical and cloud integration testing.

## Cloud teammate handoff

The target is the existing [cloud OpenAPI 0.4.1](https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/docs),
not a request to create new APIs. The earlier five `/api/rfid/...` endpoints
in [RFID_API_HANDOFF.md](../RFID_API_HANDOFF.md) describe a different local
workflow; they are not listed in this cloud OpenAPI. Their deployment
elsewhere has not been verified.

| Purpose | Existing cloud route | Final firmware status |
| --- | --- | --- |
| Upload a card event | `POST /api/rfid-events` | Implemented; real device/token access needs testing |
| Get a pending label | `GET /api/commands?device_id=...&type=label&status=pending` and `GET /api/commands/{command_id}` | Implemented behind `ONLINE_LABELS_ENABLED`; integration and recovery are not verified |
| Confirm a label | `POST /api/commands/{command_id}/acknowledgements` | Implemented after correlated display completion; not hardware/cloud tested |
| Get a pending assignment | `GET /api/commands?device_id=...&type=assignment&status=pending` and `GET /api/commands/{command_id}` | Implemented behind `ONLINE_ASSIGNMENTS_ENABLED`; not hardware/cloud tested |
| Confirm an assignment | `POST /api/commands/{command_id}/acknowledgements` | Implemented only after Mega confirms persistence and activation; not hardware/cloud tested |
| Recover an unresolved assignment | `GET /api/commands` with `status=recovery_pending`, then `POST /api/commands/{command_id}/recovery-acknowledgements` | Implemented behind `ONLINE_ASSIGNMENTS_ENABLED`; requires updated Mega firmware and has not been hardware/cloud tested |

Confirm with the cloud teammate that the deployed version matches this contract,
that the showcase is bound to the intended device, and that its hardware bearer
credential is authorized for RFID and its own assignment/label command reads and
receipts. Device status for recovery uses `POST /api/device-status`. The device
uses `source=device`; staff credentials must not be put into firmware. Do not
send credentials in the handoff. HTTP 401 without a credential only demonstrates
an authentication response, not that the real credential or end-to-end flow works.
Assignment and label commands require separate durable generation fences, exact
reads and correlated device outcomes; see the
[producer contract](../backend/docs/PRODUCER-RESPONSIBILITIES-v0.4.md).
An expired pending command keeps its lock and requires recovery, not a late
success receipt.

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
| Two RGB LEDs in R/G/B order | Mega **40/42/38** (proximity / card feedback) and **36/34/32** (light warning), active high. If your harness follows the original sensor sketch, set `LED_PINS` to `{13,12,11,48,46,44}` in Mega_Final.ino. Set `LED_ACTIVE_HIGH=false` for common-anode wiring. |
| RC522 | Mega D53 SS/SDA, D49 RST, D51 MOSI, D50 MISO, D52 SCK. Use 3.3 V power and level-shift Mega outputs for a 3.3 V reader. |
| Door relay module | Mega D47 IN, active low by default. Match `doorRelayActiveLow` in RfidAccess.h to the module. Test the relay indicator with the lock load disconnected first. |
| SSD1306 OLED, 128×64 I²C | Mega D20 SDA, D21 SCL. Addresses 0x3C and 0x3D are probed. Use power and I²C levels rated for your breakout. |
| Mega-mounted e-paper, RFID-lock wiring | Mega D51 MOSI, D52 SCK, D43 CS, D41 DC, D39 RST, D37 BUSY. RC522 shares SPI and has its own CS. |
| ESP-mounted e-paper, alternative wiring | ESP GPIO11 MOSI, GPIO12 SCK, GPIO10 CS, GPIO9 DC, GPIO8 RST, GPIO7 BUSY. |

Use the GDEH0154D67 1.54-inch 200×200 B/W panel and its SPI driver board. Use 3.3 V logic or a level-shifting breakout for Mega outputs. Connect a panel to **one** board. Set `RFID_PANEL_ON_MEGA` in DisplayConfig.h to match its location. RFID taps no longer trigger the local server/e-paper workflow.

## First test without Wi-Fi

1. Upload both sketches. Open both monitors. Within about ten seconds the Mega should print `To ESP32: V1,...` and the ESP should print `Mega seq=...`. Compare the sequence numbers. With no Wi-Fi credentials the ESP prints `WIFI NOT CONFIGURED`; UART reception continues.
2. Move an object inside 15 cm of the ultrasonic sensor: the first RGB LED turns white. Auto lighting clears after a second of valid far readings, or immediately on an invalid echo. Send `O`, `F`, and `A` to the **Mega USB** monitor for manual on, off, and auto of this white light. `lighting_output` reports the actual white light, not warning or card feedback. The second RGB LED turns yellow when light is below its configured range, red when above it, and off when in range. It responds on the next sensor reading, without waiting for the warning hold. The sensor is normally read once per second; OLED warnings still use the configured hold. Without a cloud profile, the default limits and five-second warning hold apply. Check the OLED and the next ESP reading.
3. Tap the configured door card. Expect an accepted door event on both monitors and a roughly three-second relay pulse. Tap an unregistered card and expect a denied event with the relay staying locked. An accepted e-paper card makes the first LED green for one second, temporarily replacing white; the second LED keeps showing any light warning. This is card feedback, not confirmation of a panel refresh. The Wi-Fi test card is ignored by the ESP and sends no network request. Card mappings are in rfid_cards.h.
4. To exercise EEPROM learning, scan a spare unlisted card and send `M` for an edit card or `L` for a door card on Mega USB. Reset, rescan, and check the role. `D` lists roles. `X` clears all learned cards. Cards with a fixed role, including the explicitly denied blue tag, are configured in rfid_cards.h.
5. For a Mega-mounted panel, send `P` to the Mega USB monitor. Expect the bench text and `Panel OK`. For an ESP-mounted panel, set `EPAPER_BENCH_TEST=true` with `ONLINE_LABELS_ENABLED=false`, upload, and inspect the printed BUSY result and the image. Restore the flag after the test. Both tests replace the current panel image. Space full refreshes at least three minutes apart.

## Cloud RFID taps

`CLOUD_RFID_ENABLED` in DisplayConfig.h enables the cloud-only
`POST /api/rfid-events` workflow. No local server address is needed; local RFID
forwarding is disabled by `LOCAL_RFID_ENABLED=false`. The Mega still decides
whether a door card can open the relay; the cloud records events but does not
authorize or remotely operate the physical lock. Wi-Fi test cards are ignored.

1. Copy `ESP32S3_Final/local_config.example.h` to `local_config.h`; enter the
   Wi-Fi credentials and the hardware producer Bearer token. Never use a staff
   Cognito token or commit the real config.
2. Ask the backend owner to authorize `DEVICE_ID` (`reshow-mega-01`) for
   `POST /api/rfid-events` and provision the matching hardware token. This
   endpoint records taps; it does not unlock the Mega relay.
3. Upload the ESP32-S3 sketch. In Serial Monitor at 115200 baud, wait for
   `WIFI CONNECTED` and UTC synchronization, then tap a card. `CLOUD RFID
   accepted HTTP 201` means created; HTTP 200 means an identical retry was
   already recorded.

The payload uses a stable event ID and UTC `recorded_at`. Accepted events send
their `door`, `epaper` or `edit` role; denied events omit it. Card UIDs are not
sent. The ESP verifies the server certificate and retries the same event after
transient failures. Taps received before NTP synchronization are retained in
RAM and timestamped once UTC becomes available. The 12-event queue is not
persistent across a reboot. Serial Monitor reports token, permission, binding,
queue and HTTP errors without printing the token.

The cloud queue holds up to 12 events in RAM, including the event being
retried. A tap received when it is full generates a serial error; a reboot
loses queued events. A rejected event stays at the head of the queue for
operator investigation, so later events may fill the queue. Cloud event
logging does not control the Mega relay; cloud label commands are a separate
optional workflow.

## Cloud assignment and Mega e-paper

`ONLINE_ASSIGNMENTS_ENABLED` defaults to `false`. To bench-test a fresh
assignment, configure the matching `SHOWCASE_ID`, `DEVICE_ID`, Wi-Fi and
hardware token, enable the flag, then compile and upload **both** final
sketches. Confirm the two-way UART wiring and permissions first. The ESP prints
`ASSIGNMENT: cloud acknowledged the Mega profile.` only after receiving an
accepted cloud receipt. A successful compile or upload is not an end-to-end
test. Check the actual Mega warning limits and the webpage before relying on
them.

An existing pending command may have expired while the device was offline.
Do not submit a late success or repeatedly create new assignments. An expired
or recovery-pending command blocks the executor. The local website Activity
page can request recovery using the existing staff API; this does not release
the lock. Website changes must be deployed before appearing on the live site.

Assignment recovery is a separate opt-in: `ONLINE_ASSIGNMENT_RECOVERY_ENABLED`
defaults to `false` and must remain disabled until an isolated hardware/backend
test is completed. After both boards have the matching firmware, the ESP persists the cloud fence
and asks the Mega to verify the exact profile, or to confirm that its intact
profile store is older or empty. It then uses a fresh post-fence device status
and submits the corresponding recovery receipt. This assumes the same Mega and
its intact EEPROM; replacing the board or erasing its store destroys evidence
needed for reconciliation. An unknown result, invalid storage, changed binding,
or rejected receipt stays blocked. Do not erase fences blindly. This recovery
path has not been compiled or tested against hardware or the deployed backend.

Profiles use nonnegative centi-degree temperature limits and 10-bit light ADC
limits. The local caution and daily swing warnings are disabled while a cloud
profile is active. The Mega emits the actual exhibit identity with each
subsequent measurement; when assignments are enabled, old-format samples with
unknown exhibit identity are omitted from historical readings instead of being
relabeled. Device status is still uploaded.

The cloud label bridge is disabled until configured. Keep `RFID_PANEL_ON_MEGA=true`,
configure the matching `SHOWCASE_ID`, `DEVICE_ID`, `EXHIBIT_ID`, Wi-Fi and
hardware token, then enable `ONLINE_LABELS_ENABLED` and upload the ESP sketch.
The backend must authorize the hardware token for command reads and receipts,
bind the device to the showcase and already have a confirmed exhibit assignment.
When both cloud flags are enabled, the label must match the assignment verified
by the Mega. A pre-existing assignment not recorded in this ESP journal needs
device-side reconciliation; it must not be assumed from a hard-coded exhibit ID.

In the Live website, sign in as Operator or Administrator, open RFID & epaper
and select the showcase under RFID and e-paper. Tap a door or e-paper card,
enter a short description and publish. The cloud queues a label command; the
ESP validates it, sends the title, optional collection and description to the
Mega, then reports success only after the Mega confirms its display BUSY cycle.
The entire text including ` | ` separators must fit 180 printable ASCII
characters; images are unsupported. For a registered `reshow-mega-01` device
the backend enforces that limit too.

Full refreshes have a three-minute cooldown, including after an ESP reboot.
On a timeout, Mega restart or display BUSY fault the result is uncertain: the
ESP blocks further label execution and reports the error on Serial Monitor.
Inspect the physical panel and reconcile the command with the backend owner;
do not erase the journal or assert a success receipt. This path requires the
two-way UART wiring above and still needs an end-to-end hardware/cloud test.

## Optional AWS tests

Set the device token in local_config.h. Confirm `DEVICE_ID` and `EXHIBIT_ID` in ESP32S3_Final.ino with the backend owner, then enable `HARDWARE_UPLOADS_ENABLED`. The default exhibit is `helmet-001` from the RFID prototype. Wait for Wi-Fi and NTP. Expect HTTP 200/201 for device status and for readings with valid temperature and occupancy.

For an ESP-mounted label, set `RFID_PANEL_ON_MEGA=false` and follow the same cloud configuration steps. The first refresh can take three minutes after boot. The original [display and recovery notes](https://github.com/ee3070-team/reshow/blob/8927fef4f8325418b7973ef6d7b44fe3c5184822/hardware_updated/DISPLAYS.md) describe the AWS command protocol.

## Wi-Fi v2 troubleshooting

The ESP reads `local_config.h` when present, otherwise `local_config.example.h`. Keep real credentials in the ignored local file. After editing, upload `ESP32S3_Final.ino` again; changing a file on the PC does not change firmware already on the board.

Open the 115200-baud Serial Monitor and press RESET. After about two seconds expect `ReShow WiFi v2 | config=local_config.h | WiFi credentials=loaded`. The same banner reports the AWS flag and configuration status without printing the password or token. If you still see the old `UPLOADS DISABLED: provision a hardware producer...` line, that message belongs to the previous firmware; check the sketch folder, selected board/port, and successful upload result. ROM boot messages alone do not prove this application is running. If your working sketch uses native USB CDC, retain the same USB CDC on boot and port settings for this sketch.

`WIFI ASSOCIATED` means the hotspot was joined; `WIFI GOT_IP` and `WIFI CONNECTED: IP=...` confirm DHCP succeeded. `WIFI DISCONNECTED: reason=...` provides the driver reason for diagnosis. Wi-Fi startup now follows the working `WIFI.ino`: station mode, automatic reconnect, and `WiFi.begin` with the saved credentials. The main loop reports status instead of forcing a reconnect every 15 seconds. AWS credentials/IDs and NTP are checked separately after Wi-Fi connects. See the [Espressif Wi-Fi API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html) for the underlying connection and event APIs.
