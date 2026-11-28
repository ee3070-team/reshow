# Backend requirements: synchronize exhibit limits to the physical showcase

Date: 24 November 2026 (Hong Kong)
Project: ReShow / EE3070
Audience: backend teammate and firmware integrator

## 1. Requested outcome

When staff apply an exhibit profile from the website, the physical Mega must use
that profile's temperature limits, brightness limits and warning delay for its
SSD1306 OLED warnings. The website must distinguish saved settings, a pending
device update, and settings confirmed as applied by the device.

```text
Website -> AWS assignment/profile command -> ESP32-S3 -> UART -> Mega
Website <- AWS acknowledgement            <- ESP32-S3 <- Mega applied receipt

Sensors -> Mega -> ESP32-S3 -> AWS -> website (existing telemetry)
```

RFID, staff-card access and latch control are not required for this integration.
Staff select the exhibit through the website; a physical RFID scan must not be
required to apply its settings. Existing RFID functionality elsewhere is unchanged.

## 2. Starting point and scope

The merged firmware is under `hardware_updated/Mega2560_Sensors/` and
`hardware_updated/ESP32S3_AWS/` (PR #20). Currently:

- The Mega uses local constants: minimum 18°C, high warning at 24°C, caution at
  22°C, brightness ADC 300–600, and a five-second sustained-warning delay.
- Website profile edits do not update these constants.
- The ESP implements label commands, but does not execute profile assignments or
  forward limits to the Mega. Its telemetry exhibit ID is fixed in configuration.
- The board-to-board UART is currently one-way, Mega to ESP.

Contract baseline: the repository's `backend/backend/openapi.json` inspected for
this handoff describes API v0.4.1. It already defines `ManagementExhibit`,
`AssignmentStoredPayload` (`exhibit_id` plus a `profile` snapshot), command
polling, device fencing, acknowledgements and recovery. Reuse these contracts
where possible. This document is a requested integration specification, not a
claim that the live backend already grants these permissions. Backend owner must
confirm differences against the currently deployed version.

## 3. Backend deliverables

### A. Device-scoped authorization

Provision a hardware producer token bound to the actual device and showcase.
Keep existing real telemetry permissions and extend it for the following workflow:

| Operation | Required scope |
| --- | --- |
| `POST /api/device-status` | Existing: its device, `source=device` |
| `POST /api/readings` | Existing: its device, valid exhibit attribution, `source=device` |
| `GET /api/commands?device_id=...&type=assignment&status=pending` | Its assignment commands only; support pagination |
| `GET /api/commands?device_id=...&type=assignment&status=recovery_pending` | Its assignment recovery work |
| `GET /api/commands/{command_id}` | Strong read of its permitted command |
| `POST /api/commands/{command_id}/acknowledgements` | Its command, matching device, `source=device` |
| `POST /api/commands/{command_id}/recovery-acknowledgements` | Its command and matching recovery/fence, once firmware supports recovery |

Also confirm equivalent device-scoped label polling/read/acknowledgement access
for the existing e-paper integration. Do not grant hardware tokens staff
management or command creation. Staff continue using Cognito on `/staff-api`.
Never use the demo token for physical hardware or submit real evidence as demo.
Reject access to another device's commands even if the caller guesses its ID.

### B. Versioned, immutable profile delivery

Use the existing assignment command with an immutable `payload.profile`
snapshot. The device must not fetch a newer mutable profile and accidentally
acknowledge it as the version originally requested.

The snapshot must provide these existing fields:

| Field | Meaning / validation |
| --- | --- |
| `payload.exhibit_id` | Selected exhibit identifier |
| `payload.profile.exhibit_id` | Must match the selected exhibit |
| `payload.profile.version` | Positive profile version, maximum 2147483647 |
| `temperature_min_c` | Celsius; existing API range -40 to 125 |
| `temperature_max_c` | Celsius; existing API range -40 to 125; greater than minimum |
| `brightness_min_percent` | Relative brightness, 0–100 |
| `brightness_max_percent` | Relative brightness, 0–100; greater than minimum |
| `warning_seconds` | Integer 1–3600 |

Reject missing fields, non-finite numbers and invalid ranges. Preserve the full
existing profile schema and command envelope, including `command_id`,
`device_id`, `showcase_id`, `type=assignment`, `status`, `protocol`, `lock_id`,
`resource_generation` and `execute_before`.

Example relevant payload excerpt (not a complete POST request or full profile):

```json
{
  "exhibit_id": "artifact-01",
  "profile": {
    "exhibit_id": "artifact-01",
    "version": 7,
    "temperature_min_c": 18,
    "temperature_max_c": 24,
    "brightness_min_percent": 30,
    "brightness_max_percent": 60,
    "warning_seconds": 5
  }
}
```

Saving a profile must not mark it as applied. Support an explicit apply/reapply
operation for the currently assigned exhibit after editing its limits, even when
the exhibit ID has not changed. A new version requires a new immutable command
ID; retries of the same request reuse its ID. Keep existing locking and expiry
rules. Do not silently mutate pending commands when the profile changes again.

### C. Confirm only after the Mega has applied the settings

The ESP will acknowledge success only after the Mega validates, durably stores
and activates the complete settings snapshot and returns a matching receipt.
Receiving JSON on the ESP or sending UART bytes is not sufficient evidence.

Retain the existing acknowledgement schema:

```json
{
  "acknowledgement_id": "ack-example-01",
  "device_id": "reshow-mega-01",
  "recorded_at": "2026-11-24T08:00:00Z",
  "source": "device",
  "success": true,
  "message": "Mega applied exhibit artifact-01 profile version 7"
}
```

Example IDs and timestamp are illustrative. The backend obtains the confirmed
profile/version from the command snapshot, not by parsing the message string.
Identical acknowledgement retries must be idempotent. Conflicting retries must
fail explicitly. Validation rejection leaves the previous settings confirmed.
Lost UART receipts and reset-during-apply outcomes are uncertain, not proof that
nothing happened: retain the command lock and use recovery.

Keep `device-fence-v1`: exact-command read before execution, durable generation
fences, serialized execution/recovery per resource, and no execution after
expiry. Confirm how assignment and label operations share/order their resource
locks so a stale label cannot be applied after a different exhibit assignment.
Do not automatically unlock expired commands or fabricate a device receipt.

### D. Return desired and confirmed state to the frontend

Existing staff read responses should expose enough information to distinguish:

- Saved profile version and desired limits.
- Pending command and requested profile version.
- Confirmed exhibit, applied profile version and acknowledgement time.
- Last confirmed limits, either explicitly or through the immutable applied
  snapshot. Do not look up the latest mutable profile to represent applied limits.
- Failed, expired or recovery-pending delivery and stale/offline device status.

Reuse fields such as `confirmed_profile_version` where available. Document any
additional response fields in OpenAPI and provide frontend mapping examples.
An offline device retains its last applied profile; saving a new profile online
does not change confirmed state. Applying monitoring settings alone must not
claim that the e-paper label refreshed; retain the existing label reconciliation
state until a separate label receipt confirms it.

### E. Correct telemetry attribution when assignment changes

The firmware must stop relying on a single fixed exhibit ID once assignments
are supported. Coordinate a transition rule with the firmware integrator:

- Samples carry the exhibit/profile identity active when acquired.
- Buffered readings from the old assignment retain the old exhibit identity.
- Samples collected during an uncertain transition are explicitly handled, not
  silently attributed to the new exhibit.
- Device status and assignment acknowledgements must not make stale queued data
  look like current measurements or overwrite newer confirmed state.

Confirm which existing reading/status fields can express this. If profile
version or assignment ID must be added, publish a backward-compatible schema
change before firmware sends it; current strict schemas reject unknown fields.

## 4. Threshold semantics to agree before implementation

Proposed behavior for both firmware and frontend explanations:

- Low temperature: `temperature < temperature_min_c`.
- High temperature: `temperature >= temperature_max_c` (preserves current firmware).
- Brightness warning: below minimum or above maximum; endpoints are allowed.
- Each condition must persist continuously for `warning_seconds`; recovery
  clears it. Invalid sensors show a separate fault and reset the sustained timer.
- Applying a new profile resets the warning timers and temperature-swing window
  so history from the previous exhibit does not trigger the new exhibit's warning.

The Mega light ADC is 0–1023. Its percentage is `100 * ADC / 1023`, not lux.
For integer limits use `ceil(min_percent * 1023 / 100)` and
`floor(max_percent * 1023 / 100)`, warning outside that raw interval. Reject a
range that contains no representable ADC value. Do not scale Mega data as 12-bit
ADC (0–4095). Confirm whether the physical sensor increases with brightness.

Temperature is represented in centi-degrees in the UART firmware. Agree on
precision/rounding; proposed maximum two decimal places for applied temperature
limits. The API permits negative temperatures, but the current single-supply
LM35 circuit is intended for positive room temperatures: unsupported profiles
must fail visibly instead of being clamped silently.

The existing 22°C caution threshold and 2°C daily-swing warning have no matching
fields in this profile contract. Proposed first release: disable these fixed
extra warnings once a remotely managed profile is applied, or explicitly add
versioned configuration for them. Do not leave a hidden fixed 22°C caution that
contradicts a staff-selected range. Backend/firmware owners must agree on this.

## 5. Firmware work owned by the firmware integrator

The backend teammate is not being asked to implement the Arduino code. Firmware
work will include:

1. Add ESP-to-Mega UART wiring with suitable voltage-level interfacing.
2. Implement bounded, checksummed settings messages and correlated applied receipts.
3. Validate and atomically persist profile identity, limits, version and fences
   on the Mega; retain the previous valid settings on failure.
4. Restore applied settings after reboot and continue warnings during Wi-Fi loss.
5. Implement ESP assignment polling, durable retry/recovery state and acknowledgement
   only after the matching Mega receipt. Never acknowledge unsupported commands.
6. Preserve sensor sampling/upload operation during settings delivery, and tag
   queued data with its acquisition-time exhibit identity.

No valid saved profile on first boot must be visible as an unconfigured/default
state. The existing bench defaults must not be described as cloud-confirmed.

## 6. Separate optional work: cloud warning-event records

Applying thresholds and showing OLED warnings is the required first delivery.
Recording warning history in the website is a separate extension: raw readings
alone do not prove that the device's sustained-warning timer activated.

If included, grant scoped `POST /api/warnings` access with `source=device` and
agree on immutable interval IDs, onset/clear timestamps, exhibit attribution,
offline buffering and reset/missing-data behavior. The existing interval schema
requires an end time; do not invent one for a warning that is still active.
Document any separate active-warning representation needed by the frontend.

## 7. Acceptance tests

| Scenario | Required result |
| --- | --- |
| Apply valid profile to online device | Mega uses exact limits/delay; website confirms matching version only after its receipt |
| Edit limits for the same exhibit | Reapply sends a new snapshot/version and changes physical warning behavior |
| Save without apply | Desired profile changes; confirmed device settings remain unchanged |
| Brief vs sustained breach | No threshold warning before configured delay; warning after continuous breach |
| Threshold endpoints | Temperature and brightness equality behave exactly as agreed above |
| Invalid/missing/unsupported settings | No partial apply; explicit failure; old valid settings retained |
| Another device's token | Poll/read/ack rejected; no information or state change across devices |
| Wi-Fi outage and reboot | Last applied settings restored locally; frontend does not claim newer settings applied |
| Lost Mega receipt / reset during apply | No false success/failure assertion; fenced recovery establishes actual outcome |
| Lost HTTP response | Same acknowledgement ID/body retried without applying twice |
| Old generation / expired command | No application; lock/recovery behavior follows device-fence contract |
| Empty filtered page with cursor | Poller continues through pagination and finds relevant work |
| Assignment switch with queued samples | Old readings retain old exhibit identity; new readings use new identity |
| Settings applied but label unchanged | Frontend shows appropriate label reconciliation state |

## 8. Requested handoff from backend teammate

Please provide:

- Confirmed API origin, deployed contract revision and route permissions.
- Actual device/showcase/exhibit identifiers and a privately delivered hardware
  token (never commit a real token to this document or GitHub).
- Full sanitized assignment command and acknowledgement examples, including
  version, generation, deadline and lock fields.
- Example pending, confirmed, failed and recovery-pending staff responses.
- Confirmed threshold boundaries, numeric precision and treatment of caution/swing.
- Recovery procedure and errors for denied, expired, conflicting and invalid requests.
- Backend test evidence for authorization, immutable snapshots, same-exhibit
  reapply, acknowledgement idempotency and confirmed-state updates.

Once these are agreed, the firmware integration can be implemented and tested
end-to-end against a provisioned physical device. This document does not enable
cloud permissions, deploy backend changes or alter the existing firmware.
