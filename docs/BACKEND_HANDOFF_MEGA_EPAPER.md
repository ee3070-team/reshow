> Update - 27 November 2026: The local `hardware_updated/Final` firmware now implements persistent assignment/profile commands and forwards AWS labels to the Mega-mounted panel. Scoped hardware assignment/label routes are already present in the repository backend. The historical gaps listed below describe the earlier handoff; use `hardware_updated/Final/README.md` for the current upload/configuration and bench-verification steps. Deployment, real device provisioning and physical verification are still required. Automated device recovery acknowledgements remain unimplemented.

# Backend handoff: online labels for the Mega-connected e-paper

Prepared: 24 November 2026, Hong Kong time.

## Requested outcome

An authorized staff member edits an exhibit label on the existing website and
clicks **Send to showcase**. AWS stores an immutable command. The ESP32-S3 fetches
it, transfers the label to the Arduino Mega, and the Mega refreshes the physical
GDEH0154D67 e-paper. Only after the Mega confirms completion does the ESP submit
a success receipt to AWS. The website then shows the label as acknowledged.

Keep the existing sensor upload path working:

```text
Sensors -> Mega -> ESP32-S3 -> AWS -> website

Website -> AWS label command -> ESP32-S3 -> UART -> Mega -> e-paper
Website <- AWS acknowledgement <- ESP32-S3 <- Mega refresh result
```

This handoff requests backend work for online labels. RFID, lock control, remote
lighting, remote environmental-profile application and cloud warning-event
uploads are not required for this delivery. OLED warnings remain local on Mega.
Do not make RFID scans a prerequisite for sending a label from the website.

## Current evidence and remaining work

The user reports the Mega-connected panel displays the local ReShow e-paper
test image. The ESP-to-Mega return cable has now been added. This demonstrates a
local image was drawn; it does not prove AWS delivery or UART return-path operation.

The following findings come from the local repository source, not a new test of
the deployed AWS service. Please compare them with the actual deployed revision.

| Area | Current state | Required next action |
| --- | --- | --- |
| Hardware token authorization | `backend/backend/auth.py::authorize_hardware` allows only POSTs in `HARDWARE_POST_PATHS` | Add narrowly scoped label command reads and receipts |
| Command lifecycle | Existing commands, immutable label payloads, acknowledgements, expiry and recovery | Reuse these mechanisms; preserve their guarantees |
| ESP AWS label handler | `OnlineLabel.h` refreshes an ESP-connected display | Firmware owner must replace that execution path with Mega transfer/completion handling |
| Mega UART label transfer | `MegaPanel.h` and `RfidBridge.h` implement a LAN-workflow transfer limited to 180 characters | Firmware owner must adapt it to AWS commands and strengthen retry/reset handling |
| Physical display | GDEH0154D67, 1.54-inch, 200×200, connected to Mega | Keep this wiring; AWS does not need a second device credential for Mega |
| Online label settings | `ONLINE_LABELS_ENABLED=false`; `SHOWCASE_ID` is a placeholder | Enable only after backend setup and Mega forwarding are ready |

**Backend changes alone will not make the current firmware update the Mega panel.**
The backend and firmware work can proceed in parallel. Do not enable the current
AWS handler as a substitute for implementing the Mega forwarding path.

## 1. Provision and return the real device configuration

Please provide these values to the firmware owner:

| Value | Current firmware value / action |
| --- | --- |
| API base URL | `https://urauw4ckw1.execute-api.us-east-1.amazonaws.com`; confirm deployment |
| `DEVICE_ID` | `reshow-mega-01`; confirm this is the registered physical device |
| `EXHIBIT_ID` | `helmet-001` in the Final firmware; confirm the actual installed exhibit |
| `SHOWCASE_ID` | Not configured; provide the registered showcase ID |
| Device bearer token | Provision or confirm privately; never include the token in this Markdown, Git or browser code |
| Confirmed assignment | Provide the confirmed exhibit ID and its actual assignment command ID |
| Deployed revision | Commit/version and deployment time containing the label permissions |

The credential represents the ESP/Mega showcase pair. Continue to require
`source="device"` for its real measurements and acknowledgements. Do not use the
shared synthetic demo credential, a staff Cognito token, or AWS access keys in firmware.

The existing enrollment helper is `backend/scripts/manage_hardware_device.py`.
Review its enrollment/credential metadata and tests when adding capabilities;
do not simply broaden the demo producer's permissions.

### Assignment prerequisite must be resolved explicitly

The current website only sends a label for the showcase's confirmed exhibit.
The backend freezes the associated `assignment_command_id` into the label payload.
Our current label integration does not execute assignment commands or apply
cloud environmental profiles to Mega.

Please confirm whether a valid real-device assignment already exists. If it does
not, agree a supported provisioning/assignment workflow with the firmware owner
before the label test. Do not fabricate a successful assignment receipt or mark
an unapplied profile as confirmed just to unlock the label button. If the existing
contract requires physical profile application, that is an additional firmware
prerequisite, not something label authorization can bypass.

## 2. Extend hardware authorization for these routes

All device requests use HTTPS and `Authorization: Bearer <device-token>`.

| Method and route | Device access required |
| --- | --- |
| `POST /api/device-status` | Preserve existing sensor/status access |
| `POST /api/readings` | Preserve existing reading access |
| `GET /api/commands?device_id=<own-id>&type=label&status=pending&limit=1` | List this device's pending label commands |
| `GET /api/commands/{command_id}` | Read only this device's label command, including full execution metadata |
| `POST /api/commands/{command_id}/acknowledgements` | Submit the result of this device's label command |

Support opaque `cursor` pagination. A filtered page may be empty while still
having `next_cursor`; the producer must be able to continue through it.

Recovery support must also be agreed before operation: scoped polling of
`status=recovery_pending` and
`POST /api/commands/{command_id}/recovery-acknowledgements` should be available
to the authorized device recovery executor. The current label firmware does not
implement automatic recovery; retain a documented manual producer recovery process
until it does. Staff recovery requests remain staff operations.

### Required authorization checks

- Resolve ownership from the authenticated hardware credential, not a client-supplied ID alone.
- Require the list query's `device_id` to match the token. Reject missing or
  different device IDs; do not allow an unscoped list. Restrict results to labels.
- For exact reads and receipts, load the command and check its stored device and
  type before returning data or applying changes. Body validation alone is insufficient.
- Reject a receipt whose `device_id` differs from the token or command, or whose
  `source` is not `device`.
- Scope cursor reuse to the authorized device and filter query.
- Deny hardware access to staff management, card/lighting/assignment commands,
  command creation and unrelated device data unless separately designed and authorized.
- Preserve revocation, rate limits, immutable-ID duplicate behavior, expiry and
  recovery locks. Do not globally weaken `/api` or `/staff-api` authorization.

The device needs the producer execution view, including `lock_id`,
`resource_generation`, `protocol`, `execute_before` and immutable `payload`.
The reduced staff operational view is not an adequate replacement.

## 3. Keep the existing command schema

No new AWS endpoint is needed simply because the display is on Mega. The UART
protocol is internal to the hardware pair. Continue using the existing staff
label-draft and command routes and the existing producer receipt route.

When staff sends a label, copy the selected draft version/content into the
command. Later draft edits must not change an outstanding command's content.
Return the same snapshot on an exact command read. Do not make firmware fetch
the newest draft to execute an older command.

Example execution response below is illustrative: IDs, generation and times must
be real server-generated/bound values, and the deadline must be in the future.

```json
{
  "item": {
    "command_id": "label-example-001",
    "device_id": "reshow-mega-01",
    "showcase_id": "REPLACE_WITH_REGISTERED_SHOWCASE",
    "type": "label",
    "status": "pending",
    "protocol": "device-fence-v1",
    "lock_id": "showcase#REPLACE_WITH_REGISTERED_SHOWCASE#label",
    "resource_generation": 1,
    "execute_before": "2026-11-24T08:05:00Z",
    "payload": {
      "exhibit_id": "helmet-001",
      "draft_version": 1,
      "assignment_command_id": "REPLACE_WITH_REAL_ASSIGNMENT_COMMAND",
      "content": {
        "title": "Historic Helmet",
        "collection": "School Collection",
        "description": "A protective helmet displayed for our exhibition.",
        "image_media_id": null
      }
    }
  },
  "consistency": "strong"
}
```

The existing ESP reader bounds each response body to 16 KiB, uses `limit=1`,
and accepts continuation cursors up to 2048 characters. Keep responses within
those limits or agree corresponding firmware changes. Preserve API errors and
their HTTP status codes rather than returning a success-shaped error document.

## 4. Agree the physical label content limit

The website's API permits more text than the current Mega transport can carry.
These are different limits:

- API label fields: title up to 100 characters, collection up to 120, description
  up to 2000, optional image reference.
- Existing ESP-only renderer: 31 columns × 22 rows, including heading/collection
  and a separator; printable ASCII plus newline, no images.
- Existing Mega LAN transfer: **180 characters total**, using printable ASCII
  chunks. It currently transfers a single description string, not all label fields.

**Proposed initial Mega online-label contract, to confirm with firmware owner:**
render `title + " | " + collection + " | " + description`, omitting an empty
collection and its separator. Require printable ASCII (space through `~`), no
newlines/control characters, no image, and at most 180 characters in the combined
string including separators. The example above fits comfortably.

Keep the structured API fields; combine them in firmware. Do not silently drop
the title/collection, replace unsupported characters, ignore an image reference,
or truncate text and then acknowledge the original full content as displayed.
If richer formatting or longer text is required, extend the Mega transfer and
renderer first and agree a revised capability limit.

For this physical device, reject incompatible content before command creation
where practical and expose a clear frontend validation message. Apply a
device-specific restriction rather than reducing the shared schema for every
future display. Firmware must still validate content before any physical refresh.
For a first integration test, manually use the short ASCII example with no image.

## 5. Acknowledge physical completion, not delivery to ESP

The intended sequence is:

1. Staff sends the label; backend creates a pending command and holds its lock.
2. ESP polls, exact-reads the command, validates ownership, generation and deadline,
   and durably records execution state before any display side effect.
3. ESP transfers the validated text to Mega using bounded UART messages.
4. Mega refreshes the e-paper and checks the panel BUSY completion.
5. Mega returns a completion result associated with that specific transfer.
6. ESP persists a stable acknowledgement body and submits it to AWS.
7. Backend accepts the receipt, settles the command, and updates confirmed label
   state only for a successful result. Frontend observes that confirmed state.

Example successful receipt (illustrative timestamp and ID):

```json
{
  "acknowledgement_id": "ack-label-example-001",
  "device_id": "reshow-mega-01",
  "recorded_at": "2026-11-24T08:02:10Z",
  "source": "device",
  "success": true,
  "message": "Mega completed GDEH0154D67 refresh; BUSY completion observed."
}
```

The ESP must generate and preserve the real timestamp/ID. Retry the exact same
body/ID after a network failure; do not redraw the screen just to retry an AWS
receipt. Keep existing HTTP 200/201 acceptance and conflict behavior.

| Outcome | Required handling |
| --- | --- |
| Command fetched or UART chunks accepted | Still pending; not display completion |
| Mega confirms the final refresh for this command | Submit success receipt |
| Unsupported content rejected before any display change | Submit failure receipt; retain prior confirmed label |
| Lost final UART reply, reset during refresh, or ambiguous BUSY failure | Outcome unknown; retain unresolved state and reconcile through recovery |
| AWS receipt response lost | Retry identical receipt; do not invent another operation |
| Deadline expired or recovery already started | Honor server lifecycle rules; no late normal success override |

BUSY completion is controller evidence, not optical verification of every pixel.
During bench acceptance, also inspect the physical screen.

## 6. Timing, offline operation and recovery

The existing firmware polls every five seconds when eligible and spaces full
refreshes by at least 180 seconds, including a startup cooldown. Commands currently
expire after 300 seconds, and the ESP requires at least 30 seconds remaining to
start. Keep these timings coordinated. For the first test, boot the device, wait
for Wi-Fi/NTP and the startup cooldown, then send the label.

Do not acknowledge a command automatically because a timer elapsed. Do not release
an unresolved lock simply because the device disconnected or the deadline expired.
The panel can retain a physical image while the cloud outcome remains uncertain.

For recovery, retain the existing device-fence protocol: serialize/fence execution,
establish the actual result, publish fresh post-fence status, and submit an explicit
recovery receipt. Unknown outcomes stay unresolved. Agree who runs this process
while automatic firmware recovery is absent. Do not clear NVS/fences to bypass a
pending command or ask the browser to manufacture device evidence.

## 7. Ownership of remaining work

| Owner | Deliverable |
| --- | --- |
| Backend teammate | Scoped hardware authorization; ownership checks; real device/showcase/exhibit registration; assignment prerequisite; updated OpenAPI and tests; deployment and private configuration handoff |
| Firmware owner | Connect AWS executor to Mega UART; preserve full agreed text; correlate completion with command; handle resets, duplicate transfers, checksums/timeouts and durable execution state; keep sensor traffic working |
| Firmware owner | Prevent the LAN/RFID display path from overwriting an AWS-controlled label; serialize all writes to the single panel |
| Frontend owner | Preserve draft/requested/acknowledged distinction; explain content limits and failure/recovery states; never submit producer receipts |
| Team together | Execute the end-to-end physical acceptance test and retain evidence |

The existing UART transfer's small transfer ID and chunk acknowledgements are
useful starting points, not proof of durable exactly-once execution across resets.
Do not mark the Mega forwarding work complete by only calling the existing LAN
`draw()` function from the AWS handler.

## 8. Acceptance checklist

Backend automated checks:

- [ ] Valid hardware token can list/exact-read its own label commands and submit receipts.
- [ ] Other-device IDs, guessed command IDs, foreign cursors and non-label commands are denied.
- [ ] Missing/revoked tokens fail; `source=demo` and mismatched receipt IDs/devices are denied.
- [ ] Duplicate identical receipts are idempotent; conflicting reuse of an ID is rejected.
- [ ] Draft edits do not mutate an already queued command.
- [ ] Expiry, stale generations, recovery locks and late receipts preserve existing behavior.
- [ ] Unsupported display content has a clear validation/failure path without false confirmation.
- [ ] Existing sensor upload and staff authentication tests still pass.

Physical integration checks, after both backend and firmware changes:

- [ ] Live sensor readings continue from Mega through ESP to AWS/frontend.
- [ ] Staff sends the short example; the website initially shows pending.
- [ ] Mega renders the expected title, collection and description without clipping.
- [ ] AWS accepts success only after Mega's correlated final completion response.
- [ ] The website shows the matching acknowledged command/content/version.
- [ ] Temporary Wi-Fi loss preserves the panel image and does not fabricate success.
- [ ] Lost receipt response retries safely without a second refresh.
- [ ] A Mega reset or lost final UART response remains unknown until reconciled.
- [ ] An oversized/image/non-ASCII label is rejected clearly; no silent truncation.
- [ ] No RFID tap is needed anywhere in this online-label test.

## 9. What to send back to the firmware owner

Please return:

1. Deployed API URL/revision and confirmation of the authorized routes.
2. Device, showcase and exhibit IDs, plus confirmed assignment evidence or its
   remaining implementation requirement.
3. Hardware credential via a private channel (not in Git, screenshots or this file).
4. One sanitized real pending-label JSON response for the configured device.
5. Confirmation of the initial 180-character ASCII/no-image contract, or agreed revision.
6. Results of ownership, duplicate receipt, expiry and recovery tests.
7. The person/procedure responsible for reconciling an uncertain display outcome.

Do not report end-to-end completion until the physical checklist has passed.

## Repository references

- `backend/backend/auth.py`: hardware route authorization and body identity checks.
- `backend/backend/workflows.py`: command creation, frozen label payload, receipt settlement and recovery.
- `backend/backend/openapi.json`: current API schema (local copy identifies v0.4.1).
- `backend/docs/PRODUCER-RESPONSIBILITIES-v0.4.md`: producer/backend/frontend responsibilities.
- `backend/scripts/manage_hardware_device.py`: hardware credential enrollment.
- `hardware_updated/Final/ESP32S3_Final/OnlineLabel.h`: existing AWS label executor.
- `hardware_updated/Final/ESP32S3_Final/RfidBridge.h`: existing LAN-to-Mega transfer.
- `hardware_updated/Final/Mega_Final/MegaPanel.h`: Mega receiver and panel refresh.
- `hardware_updated/Final/ESP32S3_Final/DisplayConfig.h`: panel selection and label gates.

This document is a work handoff. It does not change backend permissions, deploy
AWS resources, enable online labels, or implement the pending Mega forwarding work.
