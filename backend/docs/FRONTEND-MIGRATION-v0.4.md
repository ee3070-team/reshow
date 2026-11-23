# ReShow frontend migration — v0.4

Backend **0.4.1 is deployed and live-verified** with the role-filtered scan, operational-activity and command reads below. A direct API matrix passed **114 checks across 113 requests** using actual hosted Administrator/Operator/Viewer code/PKCE sessions; live health and OpenAPI **0.4.1 / 90 paths / 150 schemas** matched the release. See [permission](../evidence/permission-v041-live.json), [deployment](../evidence/permission-v041-deployment.json) and [preservation/account-state](../evidence/permission-v041-preservation.json) evidence. The six showcase data screens remain browser-local demonstrations; these backend permissions do not establish that their live adapters exist.

The deployed coordinated release `fad8f52870836d592f96c2fe38b48fb1647bda1f` retains HTML/CSS/plain JavaScript, integrates the real Cognito Staff access account panel and preserves the newer StudioAPI/GLB route. Static/API commits match and the gated workflow plus production two-tab login/logout verification passed. The six original data screens remain explicitly browser-local demonstrations; connecting their backend service methods is separate. Real Cloudflare generation was not called. Signing in does not turn sample readings into device evidence.

## Authentication and API bases

- Public API/configuration: `https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/auth/config`.
- Live staff requests: the same origin plus `/staff-api`. Use the returned public Cognito configuration, authorization code with S256 PKCE and scope `reshow/staff`; send the access token in `Authorization: Bearer …`. No browser client secret or shared producer credential belongs in frontend code.
- Provider password entry, first-password change and recovery stay on Cognito. Public self-registration is disabled. Only short-lived OAuth transaction values may use sessionStorage; tokens stay in memory. The custom scope does not request OpenID identity tokens; the authoritative identity comes from `GET /staff-api/session`.
- Deployed callback: `https://dkbznu9f5nh9n.cloudfront.net/auth/callback.html`; logout: `https://dkbznu9f5nh9n.cloudfront.net/`. The isolated OAuth harness retains exactly `http://localhost:8000/auth/callback.html` and `http://localhost:8000/`. No arbitrary return URL is accepted.
- The merged local dashboard/StudioAPI server requires HTTPS, defaulting to `https://127.0.0.1:8000`. **Local staff sign-in is unsupported** until its exact trusted HTTPS callback/logout origin is separately added to Cognito, the browser allowlist and applicable API/media CORS. Those changes have not been made; production CloudFront and the existing HTTP-harness allowlists remain unchanged.
- Sign-out calls `POST /staff-api/session/logout` with `{}`, clears memory across tabs, revokes the refresh credential where available and navigates to provider logout. Application logout rejects previously issued access tokens even if the gateway still accepts their signatures. Access tokens last 15 minutes; expiry offers a new sign-in, without an automatic redirect loop.
- Every API request checks current account status and role. Website account roles are authoritative; the staff directory's descriptive role is not an alternate privilege source. Directory and website-account edits never silently change physical cards.

## Service method mapping

| Frontend workflow | Live operation |
| --- | --- |
| Current user/permissions | `GET /staff-api/session` |
| Overview/device list | `GET /staff-api/showcases`, `GET /staff-api/device-status?device_id=…` |
| Exhibit catalogue | `GET/POST /staff-api/exhibit-profiles`, `GET/PATCH /staff-api/exhibit-profiles/{id}` |
| Resolve exhibit tag | `GET /staff-api/exhibit-profiles/by-tag/{tag_id}` |
| Save label draft | `PUT /staff-api/label-drafts/{exhibit_id}` |
| Assign/apply profile, send label, request lighting | `POST /staff-api/commands`; poll its exact ID |
| Recover ambiguous pending command | `POST /staff-api/commands/{id}/recovery-requests`; await separate producer reconciliation |
| Exhibit registration scans | Administrator or Operator `GET /staff-api/tag-scans` and exact scan GET; Operators receive exhibit scans only, Viewers are denied |
| General operational activity | `GET /staff-api/workflow-activity` and `/workflow-activity/{activity_id}`; Administrator full authorized feed, Operator/Viewer allowlisted exhibition events and fields only |
| Calendar activity | `GET /staff-api/activity?device_id=…&start_date=…&end_date=…&exhibit_id=…` |
| Event report/heatmap | `GET /staff-api/reports/event` or `/staff-api/analytics/heatmap`; event selector OR device/start/end; optional exhibit |
| Save report | `POST /staff-api/report-snapshots` |
| Load/export saved report | `GET /staff-api/report-snapshots/{id}?format=json` or `csv` |
| Image upload | `POST /staff-api/media/upload-requests` → direct S3 form POST → `POST /staff-api/media/{media_id}/complete` → versioned association → `GET /staff-api/media/{media_id}`. See the [staff image upload guide](STAFF-IMAGE-UPLOAD.md). |
| Staff directory/card operations | Administrator-only `/staff-api/staff` and card commands |
| Website accounts | Administrator-only `/staff-api/accounts`, `/accounts/invitations`, `/accounts/{staff_id}`, `/accounts/{staff_id}/resend-invitation` |

Use current Swagger for exact fields, errors and examples. Human sessions cannot POST telemetry, scans, coverage, normal acknowledgements or recovery acknowledgements. The retained `/api` credential is a restricted synthetic producer, not an alternative website login. Hardware credentials require explicit provisioning in the later hardware integration.

## Role-filtered operational reads — source 0.4.1

For exhibit registration, Operators may list scans with the required `device_id` and optional `scan_type=exhibit`, then retrieve an exhibit scan by ID. Omission of `scan_type` still gives exhibit-only results. `scan_type=staff_card` or an exact staff-card read returns 403; Viewers are denied all scan reads. Administrators may read either category. Do not infer role access from a hidden button or a client filter.

Operators and Viewers may request `/staff-api/workflow-activity` with the normal device/exhibit/date/page filters and optional `type=assignment|label|lighting`. Their events expose only `activity_id`, `type`, `status`, `device_id`, `exhibit_id`, `command_id`, `version`, `created_at` and `updated_at`. Known statuses are `pending`, `acknowledged`, `failed`, `recovery_pending` and `recovered`; unknown event/status combinations and card-linked events are excluded. The full audit `message`, `identity` and `actor` are absent. Render an event label from its documented type/status rather than expecting the Administrator's free-text message. Staff/account/card/access events stay Administrator-only.

The exact `/staff-api/workflow-activity/{activity_id}` read applies the same event projection and linked-command check. Restricted records return 403; missing IDs return 404. Exact reads accept no query parameters (400), so a query cannot widen access to an event hidden from the list.

For non-admin users, command list/exact and Operator create/recovery responses use `OperationalCommand`, with only known assignment/label/lighting state. There is no `actor`, original `request`, `lock_id`, free-text `failure_reason` or recovery request/reason, and nested payload/recovery fields are projected. An exact card/unknown command read or explicit `type=card` is forbidden. Continue to show pending/failed/recovery states from the returned status and documented recovery metadata; do not depend on private audit strings.

These routes filter each bounded page on the server. An empty `items` array does not mean the feed is finished when `next_cursor` is present; no restricted-record totals are provided. Operator scan and non-admin command/activity continuations are opaque, authenticated and encrypted. They expire after 15 minutes and are bound to the current identity, role/account version, collection and normalized filters. Changing any of those, tampering, expiry or reusing an old-format cursor gives 400; restart with no cursor. A missing private-cursor key is a 503 service error, not permission to fall back to a shared credential. Unknown/malformed query filters return 400. Administrator and producer cursor behavior is unchanged.

## Versions, duplicates and pagination

Create a new management profile once; the backend atomically creates its immutable legacy exhibit snapshot under the same logical ID. No manual duplicate entry is required. Profile PATCH does not rewrite the original record, historical telemetry or saved reports. Assignment freezes the current profile/version; another acknowledged assignment applies later threshold changes. `confirmed_profile_version` says which requested version was acknowledged, not proof of physical application.

PATCH operations require current `If-Match`. The first label-draft PUT uses `If-Match: 0`; later PUTs use its current version. On 409 fetch current state, show the conflict and let the user reconcile. Never retry with a newer version while silently overwriting another edit.

One JSON object per POST; sequential requests support multiple distinct items. Reuse an ID and exactly the normalized original payload after a timeout. An identical retry is a duplicate response, not a second item; a changed payload under the same ID is 409. HTTP timeout does not prove a command or invitation failed. Follow `next_cursor` until null, including empty filtered pages. Legacy readings expose a bounded `truncated` flag rather than the workflow cursor contract.

## Reporting semantics

`exhibit_id` is supported consistently by calendar activity, heatmaps, event reports and new saved captures. Historical display intervals determine attribution, never the current assignment. Overlapping occupied durations are unioned; a switch splits duration without inventing a new start. A start belongs only to the unambiguous exhibit at the original event start. Contradictory display intervals and unknown assignment remain explicit; known other-exhibit time is outside the selected scope.

Requested ranges and evaluated ranges are separate. A current/future query uses one captured current-time cutoff; future seconds are neither missing nor confirmed zero. A saved capture retains that cutoff, filter, selected sections and mandatory completeness after later uploads or edits. A wholly future selection has no evaluated totals. Use 60-minute buckets for a 31-day desktop day/hour view; 15-minute buckets exceed the 2,048-bucket limit for long ranges. The 31-day elapsed-range and history/response-size limits still apply.

Display missing/partial/unknown assignment/future separately from confirmed quiet and no known selected-exhibit display. These are proximity events and occupied-zone durations, never unique visitors, attention or headcount. Current profile context captured with a report is explicitly not historical-settings evidence.

## Account administration

Create a staff directory record before its account invitation. Invitation IDs are immutable retry keys; `delivery: "suppress"` sends no email, while `delivery: "email"` is an explicit invitation action. Provider/database partial work remains provisioning until linked to the reserved provider subject. Resend is a separate explicit action with its own request ID; an ambiguous timeout is not proof that email was not sent. Account PATCH uses its own version and changes only authoritative role/status. Last active Administrator protection is serialized, including concurrent edits.

The authorized test Administrator `ee3070-staff` / `test@example.com` / EE3070 Staff is active, and its first sign-in is verified. Its enabled Cognito identity is `CONFIRMED`; `/staff-api/session` returned its intended active Administrator identity/permissions, and production managed sign-in, live account panel, invalid-password rejection, sign-out and provider re-prompt passed on 3 November 2026. Final provider/application readbacks confirmed it is the only enabled login and active Administrator; the earlier three synthetic identities and temporary Operator/Viewer verification accounts are disabled in both stores. No real invitation email or SMS was sent. The reserved test address cannot receive password-reset email. A real staff identity and any invitation send still require an explicit choice and authorization. The AWS-controlled bootstrap helper is the only initial administration path; there is no public bootstrap or default password.

## Final browser verification and enrollment state

The historical 27 October production checks passed callback query removal, live account table, local-demo separation, two independent Administrator tabs, cross-tab sign-out, old-JWT 401 before retirement, provider login re-prompt and zero console warnings/errors. Evidence: `../evidence/v04-frontend-browser-live.json` and `v04-coordinated-frontend-deployment.json`. Those three synthetic identities remain disabled with history retained. The current 3 November test Administrator status is recorded above. No real email/SMS was sent. Local HTTPS sign-in remains unsupported until separately configured; production remains configured.
