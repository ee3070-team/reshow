# ReShow: e-paper label + OLED warnings

Updated 17 November 2026 (Hong Kong). This extends the existing firmware pair,
using the actual displays supplied for this project. The proposal's ESP32-C6
architecture is adapted to the existing **Mega 2560 + ESP32-S3** hardware.

```text
LM35 / light / ultrasonic -> Mega -> UART -> ESP32-S3 -> AWS -> frontend
                              |
                              +-> SSD1306 OLED: local readings + warning codes

Frontend Labels / Send to showcase -> AWS command -> ESP32-S3 -> GDEH0154D67
Frontend confirmed label          <- AWS receipt <- full-refresh completion
```

Upload `Mega2560_Sensors/Mega2560_Sensors.ino` to the Mega and
`ESP32S3_AWS/ESP32S3_AWS.ino` to the ESP32-S3. Keep the accompanying headers
in their sketch folders. The old `hardware/hardware.ino` and
`SensorsProjectCode.ino` are historical sensor sketches, not additional uploads.
There is no RFID reader, tag scan, staff-card or latch code in this integration.

## Wiring

Keep all existing sensor pins and the one-way UART from `SETUP.txt`.
Power off before wiring; connect common ground. Use the physical **GPIO numbers**
on the ESP32-S3, checking that the selected board exposes these pins.

| GDEH0154D67 Waveshare board | ESP32-S3 |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| DIN / MOSI | GPIO11 |
| CLK / SCK | GPIO12 |
| CS | GPIO10 |
| DC | GPIO9 |
| RST | GPIO8 |
| BUSY | GPIO7 |

This is a **1.54-inch 200×200 black-and-white GDEH0154D67 (SSD1681)**,
using `GxEPD2_154_D67`. Other 1.54-inch panels are not interchangeable.
Use the Waveshare driver board, not a bare panel connected directly to GPIO.
Keep data/power at 3.3 V. SPI does not need MISO. BUSY is mandatory for delivery
checks. A 2 ms reset pulse supports the Waveshare reset/power circuit.
Pin constants are in `ESP32S3_AWS/DisplayConfig.h`.

| SSD1306 0.96-inch OLED (assumed 128×64, four-pin I²C module) | Mega 2560 |
| --- | --- |
| SDA | D20 / SDA |
| SCL | D21 / SCL |
| GND | GND |
| VCC | Voltage specified for your OLED breakout |

SSD1306 identifies the controller, not the breakout's voltage tolerance. For a
3.3 V-only module, power it from 3.3 V and use a bidirectional I²C level shifter
between its bus and the Mega's 5 V bus. A breakout explicitly rated for 5 V
power **and I²C logic** can connect directly. A six/seven-pin SPI OLED requires a
different constructor/wiring; this sketch targets I²C. Address defaults to
`0x3C`; change `OLED_ADDRESS` to `0x3D` if needed.

Mega D16/TX2 still connects through the existing 5 V-to-3.3 V divider to ESP
GPIO18/RX. Mega RX2 and ESP TX17 remain unconnected. No extra return UART is
needed because the ESP drives the e-paper directly.

## Libraries and settings

Install with Arduino IDE Library Manager:

| Board | Libraries |
| --- | --- |
| Mega | Adafruit SSD1306 2.5.17, Adafruit GFX Library 1.13.0, Adafruit BusIO 1.17.4 |
| ESP32-S3 | GxEPD2 1.6.4, Adafruit GFX Library 1.13.0, Adafruit BusIO 1.17.4, ArduinoJson 7.4.2 |

Wire/SPI/Preferences/WiFi/HTTPClient come with the board packages. Build targets:
Arduino AVR Boards 1.8.8, `arduino:avr:mega:cpu=atmega2560`; Espressif 3.3.12,
`esp32:esp32:esp32s3`. Both USB monitors use 115200 baud; UART remains 9600.
Do not use ArduinoJson 6 for this code. Select flash and USB settings matching
the actual ESP32-S3 board. Use an application partition large enough for the
compiler's reported sketch size.

## OLED warnings

The Mega updates the OLED once per second. Sensing, automatic lighting,
the original checksummed `V1` telemetry and local A/O/F controls continue.
Warnings require five continuous seconds outside their band; recovery clears
the warning immediately. Invalid temperature and missing sonar echo are shown
immediately. More than four simultaneous warning codes rotate through the
available rows every two seconds. An absent OLED or I²C timeout does not stop
the sensors; Serial prints the warning bit mask and display errors.

| Code | Meaning / bench default | Mask bit |
| --- | --- | --- |
| E01 | Temperature below 18°C | 0x01 |
| E02 | Temperature at or above 24°C | 0x02 |
| W01 | Temperature at or above 22°C and below 24°C | 0x04 |
| W02 | Light below ADC 300 | 0x08 |
| E03 | Light above ADC 600 | 0x10 |
| W03 | Valid temperature range exceeds 2°C in the current 24-hour window | 0x20 |
| E04 | Temperature input invalid | 0x40 |
| E05 | Ultrasonic distance unknown / no valid echo | 0x80 |

Configure limits and `WARNING_HOLD_MS` in `Mega2560_Sensors/WarningState.h`.
W03 activates as soon as the window's min/max range exceeds 2°C and clears at
the next window reset. Windows start at Mega boot; this is not a rolling 24-hour
history. Limits are demonstration defaults, not universal conservation limits.
Brightness is relative 10-bit ADC data, not lux. Sensor fault checks cannot
detect every disconnected analog sensor that happens to produce plausible data.

OLED warnings are local. The existing telemetry uploads temperature/brightness
to the website; this change does not upload warning-event intervals. The live
hardware token currently cannot POST `/api/warnings`. Website exhibit-profile
edits do **not** change these Mega thresholds: set both consistently for the
installed exhibit. Remote profile/assignment/lighting execution is outside this
label-and-OLED extension. The fixed `EXHIBIT_ID` continues to identify readings.

## Online labels and frontend use

1. Configure Wi-Fi and a **hardware** bearer token in the existing ignored
   `ESP32S3_AWS/local_config.h`. Do not put tokens in the frontend or tracked files.
2. Set `DEVICE_ID` and `EXHIBIT_ID` in the main ESP sketch. Set `SHOWCASE_ID`
   in `DisplayConfig.h`. These must describe the same physical showcase.
3. First verify the existing telemetry path with `HARDWARE_UPLOADS_ENABLED=true`.
4. Have the backend owner enable the scoped routes below and establish the real
   showcase's confirmed assignment to `EXHIBIT_ID`. This sketch only executes
   label commands, so a new website assignment request will not be acknowledged
   by it. Provisioning/reconciliation must use the backend's real-device process.
5. Set `ONLINE_LABELS_ENABLED=true` and re-upload the ESP sketch. It uses the same
   hardware bearer token for commands. Both gates default to false in shared source.
6. In the website's **Live** workspace, open **Labels**, select the assigned
   exhibit, remove any image, enter short English/ASCII text, and click
   **Send to showcase**. Saving a draft alone does not send a command. The local
   **Demo** workspace does not communicate with the board.
7. Wait for the full refresh and the AWS acknowledgement. The existing frontend
   refreshes command state; it already distinguishes requested and acknowledged
   updates. No frontend acknowledgement simulation or extra frontend route is added.

Example label:

```text
Title: Blue ceramic vase
Collection: City collection
Description: A hand-painted ceramic vase made in 1920.
             Please do not touch the exhibit.
```

The physical layout has 31 columns and 22 rows in the built-in 6×8 font, with
a separator row between heading/collection and description, border and footer.
Text wraps at word boundaries; long words wrap at character boundaries.
The whole label must fit: length allowed by the website's API is not necessarily
length that fits this small panel. Images and non-ASCII characters are explicitly
rejected with a **failed** command and a reason, preserving the previous display.
This implementation does not claim to render the website's image preview.

Only a new command causes a full refresh; polling does not refresh the panel.
Refreshes are spaced by at least **180 seconds**, including after ESP restart.
The first online label can therefore take roughly three minutes after boot.
The panel retains its last image through Wi-Fi/power loss; startup does not erase
it. The UART receiver runs in its own task while HTTPS or display refresh blocks
the main loop. The existing 24-sample telemetry queue and its overflow policy
remain in force.

## AWS backend handoff — required before live label operation

The public API contract was fetched read-only on 17 November 2026. Its version
is still 0.4.1, but it **now documents hardware producer tokens** for real sensor
POSTs. Its hardware token scope remains limited to:

```text
POST /api/device-status
POST /api/readings
```

The required additional device-scoped permissions are:

```text
GET  /api/commands?device_id=<this device>&type=label&status=pending&limit=1[&cursor=...]
GET  /api/commands/<command_id>
POST /api/commands/<command_id>/acknowledgements
```

Authorize only commands for the token's registered device/showcase, `type=label`,
and acknowledgements with `source=device` and the same device. Do not grant
hardware tokens staff management or command creation. Keep staff authentication
on `/staff-api`. Do not reuse the synthetic demo token or relabel real evidence
as `source=demo`. The current repository contains the AWS command service under
`backend/`; its device authorization needs review before enabling labels.
The root `lambda_handler.py` belongs to the separate 3D/AI studio API and does
not implement these command routes. This firmware publication changes no backend permissions.

The firmware consumes the command's immutable `payload.content` snapshot,
not the latest mutable label draft. It checks `device_id`, `showcase_id`,
`payload.exhibit_id`, `assignment_command_id`, `draft_version`,
`protocol=device-fence-v1`, `resource_generation`, `lock_id`, status and deadline.
It follows pagination, including empty filtered pages, and strongly fetches the
exact command again immediately before execution. A command needs at least
30 seconds remaining to start a refresh. The backend's expiry remains authoritative.

After a completed BUSY cycle, a receipt has this existing schema:

```json
{
  "acknowledgement_id": "a-<stable-random-id>",
  "device_id": "<configured-device>",
  "recorded_at": "<UTC-at-refresh-completion>",
  "source": "device",
  "success": true,
  "message": "GDEH0154D67 full refresh completed; BUSY cycle observed."
}
```

HTTP 200/201 confirms receipt. Retries reuse the same body/ID, including after
reset. A successful HTTP GET alone never generates a success acknowledgement.
BUSY completion is electrical driver evidence, not optical verification of pixels.

## Failure and recovery behavior

The ESP stores one NVS journal containing device binding, command ID, resource
generation, lock, execution phase and pending receipt. It persists the fence
**before** refreshing. A restart during refresh, missing/stuck BUSY, changed lock,
or rejected/conflicting receipt stops the label executor rather than claiming a
known display result. Sensor uploads continue. Acknowledgement network/auth/server
failures retain the receipt and retry after 60 seconds. A completed command is
not redrawn on acknowledgement retry.

This firmware deliberately does not implement automatic recovery acknowledgements.
If a command expires or enters `recovery_pending`, use the backend owner's
device-fence recovery process; do not fake a frontend acknowledgement. Inspect
the actual label and the `reshow-label` / `journal` NVS record, fence this executor,
establish applied/not-applied outcome, reconcile server state, and only then
service/reprovision the device with the retained generation fence. Unknown outcomes
must remain unresolved. Do not clear NVS to bypass an outstanding command or move
the same journal to another showcase. Keep only one executor for this physical
device. These fail-stop paths require a technician/backend owner; they do not
silently unlock the website or restore a possibly stale label.

## Bench verification

For an e-paper wiring test without any AWS credential, set
`EPAPER_BENCH_TEST=true`, keep `ONLINE_LABELS_ENABLED=false` and
`HARDWARE_UPLOADS_ENABLED=false`, and upload the ESP sketch. It draws one local
test label at boot and reports the BUSY result on Serial. This overwrites the
existing physical image but never sends a cloud acknowledgement. Restore the
bench flag to false before using online labels. Do not repeatedly reboot this
test faster than the three-minute refresh interval.

1. With uploads disabled, verify the OLED values and `V1` frames at 10-second
   intervals. Cover/uncover the light sensor for less than and more than five
   seconds; check recovery, E04/E05 sensor faults, and LED controls A/O/F.
2. Disconnect Wi-Fi: sensing and OLED warnings must continue. Disconnect the OLED:
   sensing must continue; reboot after repairing its wiring to re-enable it.
3. Provision/configure AWS as above. Confirm a real status and reading in the
   frontend before enabling label delivery.
4. Send the example label. Observe the panel's full refresh and then website
   acknowledgement. Send text that cannot fit: it must fail without refreshing.
5. Interrupt Wi-Fi after a refresh: the exact receipt must retry without another
   refresh. A reset while a receipt is pending must resume the receipt.
6. Test a reset during refresh only with the backend owner available for recovery:
   expect the label executor to stop, with telemetry still running.

Automated verification performed in the original firmware development workspace
(the display test file is not included in this hardware-folder publication):

```text
python -m unittest checks.firmware_test checks.firmware_displays_test -v
```

Host tests execute the actual warning/layout logic and the existing upload retry
state machine, and round-trip actual Mega frames through the ESP parser.
Arduino builds check both board targets, including an online-enabled ESP fixture
with fake credentials. The ESP fixture fits the default 1,310,720-byte application
partition. These do not replace
physical panel/BUSY, OLED/I²C, Wi-Fi or authenticated AWS testing. No board flashing,
AWS writes or deployment is performed by this source-code integration.

References: [GDEH0154D67 driver](https://github.com/ZinggJM/GxEPD2/blob/master/src/epd/GxEPD2_154_D67.h),
[Waveshare module manual](https://www.waveshare.com/wiki/1.54inch_e-Paper_Module),
[SSD1306 library](https://github.com/adafruit/Adafruit_SSD1306),
[live API contract](https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/openapi.json).
