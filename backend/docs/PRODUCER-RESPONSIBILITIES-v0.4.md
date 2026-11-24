# ReShow responsibility contract — v0.4

The following is the integration contract. Hardware firmware is not implemented or verified by this release. Synthetic producer records must use `source=demo` and never count as physical-operation evidence.

| Workflow | Hardware / explicit demo producer | Backend | Frontend |
| --- | --- | --- | --- |
| Readings/status | Publish status about every 30 seconds; publish sensor observations at the device's chosen sampling cadence. Preserve real observation timestamps when buffering. Unknown sensors are null. | Validate/store immutable IDs; show contact and measurement freshness separately; mark stale after 120 seconds. | Show null as unknown and stale values as stale, never zero/current. |
| Commands | Poll pending and recovery-pending pages for its device; exact-read immediately before execution. Serialize per resource, honor 300-second deadline and durable generation fence. Execute once and upload a separate receipt. | Freeze requested payload/version, hold resource/unique-card lock, validate receipts; no fabricated acknowledgement. | Request action and show pending/confirmed/failed/recovery state. A timeout is ambiguous. |
| Warning detection | Apply the actually acknowledged profile's temperature, relative-brightness thresholds and persistence; upload completed warning intervals. | Validate/store warnings; report coverage and current-at-capture profile caveats. | Display producer-supplied warnings without claiming backend-inferred sustained conditions. |
| Proximity | Open/close occupied-zone intervals and buffer completed records during outages. Preserve original starts. | Union intervals and attribute duration/starts using unambiguous display history. | Show events and durations, not unique visitors or attention. |
| Display history | Record actual completed display intervals after real assignment changes. | Store intervals; detect contradictory/unknown attribution. ACK alone does not invent intervals. | Distinguish requested assignment from confirmed and observed state. |
| Coverage | Submit each stream's completion assertion only after all relevant uploads have succeeded. | Preserve gaps and lower bounds; heartbeat absence/presence is not coverage. | Show quiet, partial and missing separately. |
| Tag/card scan | Echo current registration attempt nonce in a new immutable scan, correct scan type, device and observed timestamp. | Validate fresh referenced staff-card scans and reserve each scan for one command. Legacy omitted references remain explicitly unverified. | Generate a fresh attempt nonce; do not enable confirmation from an old or exhibit-tag scan. |
| Staff physical cards | Register/revoke/restore locally, then acknowledge separately; preserve uniqueness and recovery fences. | Hold active/pending card uniqueness and require producer outcome. Website roles do not grant card access. | Administrator requests explicit card changes; website disable is a separate action. |
| Login/accounts | No human-login credentials in firmware. | Cognito identity + current application roles; humans cannot submit producer evidence. | Managed sign-in, memory-only tokens and permission-aware controls. |

A 30-second status heartbeat is the recommended integration cadence; 120 seconds allows about four missed expected contacts before a stale warning. This is a project policy, not a verified hardware reporting frequency. A recent buffered upload can prove contact while its measurement is still old.

Management/device-status brightness is relative 0–100%; legacy readings are raw ADC 0–4095. The initial compatibility snapshot uses round-half-up(percent × 4095 / 100). Calibrate the actual sensor before treating this mapping as a physical control rule. Neither unit is lux.

Recovery requires the producer to durably fence the old command generation, serialize/reconcile any in-flight execution, publish a fresh post-fence status, then report an explicit applied/not_applied outcome. Unknown outcomes and absent devices keep their lock/card reservation. The backend cannot safely infer that an offline device did nothing. See Swagger recovery schemas and the workflow contract for late-receipt and stale-label rules. No remote-unlock operation exists.

## Mega e-paper label delivery

Use the existing private hardware bearer credential for the registered ESP32-S3.
The Mega shares the ESP's command channel over UART. Poll
`GET /api/commands?device_id=<registered-id>&type=label&status=pending&limit=1`
and repeat with `status=recovery_pending` for reconciliation. Follow
`next_cursor` even when `items` is empty and keep the same filters. The
deployment must have the existing workflow cursor key provisioned.

Exact-read the command before execution. Both receipt routes require the bound
device ID and `source=device`. Keep the receipt ID, timestamp and payload
stable across retries. Only submit a successful normal receipt after Mega
reports the correlated final refresh completion. A transfer acknowledgement or
a missing final result leaves the display outcome unresolved.

For `reshow-mega-01`, render the title, optional nonempty collection and
description joined by ` | `. The result must fit in 180 printable ASCII
characters, including separators. Images and line breaks are unsupported. The
draft remains structured, and the command contains the frozen version. The
showcase must already have a producer-confirmed assignment. If it does not,
the firmware owner must provide an assignment executor that applies the required
profile before confirming the assignment.

Until the firmware supports automated recovery, the staff operator requests
recovery for the unresolved command and the firmware owner coordinates device-side
reconciliation. Fence the generation durably, stop any stale execution for the
lock, establish what the display actually applied, and upload a fresh post-fence
device-status observation. Submit a recovery receipt only for an established
outcome, using the matching recovery ID, generation and observation. Keep the
lock unresolved if the physical result is uncertain. Keep online labels disabled
until the Mega-backed executor and its recovery path are ready for bench tests.

## Mega monitoring-profile assignments

The same device credential can poll `type=assignment` with `status=pending`
or `status=recovery_pending`. The exact command contains the immutable
`payload.profile`. Apply the entire snapshot once and report normal success
only after the Mega validates, persists and activates it and returns a
correlated result. Preserve the exact receipt on HTTP retry. An RFID scan is
optional. The assignment and label retain separate lock IDs and generations.
For `reshow-mega-01`, command creation checks both locks atomically so an
unresolved assignment and label cannot overlap. The ESP must still serialize
execution and recovery and validate the frozen assignment identity before
applying a label. A change to the assigned exhibit can leave a retained label
marked for reconciliation until a separate current label receipt arrives.

The website saves the desired profile via `/staff-api/exhibit-profiles/{id}`
and explicitly applies it via `POST /staff-api/commands` with
`type=assignment` and `payload={exhibit_id,profile_version}`. Older clients may
omit `profile_version`; current clients send the saved version they reviewed.
A changed version returns `409 version_conflict`. Use a fresh command ID for a
new request and the same ID and payload after an uncertain HTTP response.

| Website state | Source |
| --- | --- |
| Saved limits and version | Exact exhibit profile |
| Requested settings | Assignment `payload.profile` and command status from the staff command API |
| Applied limits and time | Exact showcase `confirmed_profile`, `confirmed_profile_version` and `assignment_acknowledged_at` |
| Failed, expired or under recovery | Command `status`, `execute_before` and `recovery`; an expired pending command still holds its lock |
| Device offline or stale | Exact showcase freshness ages and flags, evaluated separately from applied profile evidence |

The backend accepts general profiles as catalog data. To queue a Mega apply,
temperature limits must be nonnegative and exactly representable in
centi-degrees. The brightness interval must contain at least one 10-bit ADC
value. The proposed firmware boundaries are low temperature below minimum,
high temperature at or above maximum, and brightness outside the inclusive
integer interval from `ceil(minPercent * 1023 / 100)` to
`floor(maxPercent * 1023 / 100)`. The integrator must confirm sensor polarity
and apply the configured delay to continuously breached conditions. Reset
timers and swing history on apply and reset the affected timer on sensor fault.
The proposed first firmware release disables the old fixed caution and swing
warnings under a cloud-applied profile. This still needs firmware agreement
and bench verification.

Keep reading `exhibit_id`, `recorded_at` and the immutable reading ID from
acquisition through upload, including when an old sample is sent after a new
assignment. Readings from windows with unknown acquisition identity must be
held or omitted. Do not relabel them. Device status is indexed by device and
measurement time, without an exhibit or profile field. Use it for device
health and recovery observations. It carries no profile-application evidence.
The current schemas need no extra field for exhibit attribution. Per-sample
profile-version attribution would require a separate backward-compatible
contract change. Mega brightness in status is relative to the 10-bit ADC.
Preserve raw ADC and its existing ingestion representation.

Typical errors are `401 unauthorized` for missing or revoked credentials,
`403 forbidden` for out-of-scope polling or receipt identity, and
`404 not_found` for unowned or disallowed command IDs. Incompatible Mega
profiles return `400 unsupported_profile`. A conflicting immutable ID returns
`409 id_conflict`. A late normal receipt returns `409 command_expired` and
the lock remains held. After an authorized staff recovery request, the
firmware owner fences the command, establishes the actual state, uploads a
fresh post-fence status observation, and submits the matching recovery
receipt. Keep unknown outcomes unresolved.
