# Staff login integration

Backend **0.4.1 is deployed** with the role-filtered scan, operational-activity and command reads described below. The actual Lambda identity/runtime, health and live OpenAPI **0.4.1 / 90 paths / 150 schemas** were checked after the guarded AWS update. The new direct permission matrix passed **114 checks across 113 API requests**, using actual hosted code/PKCE sessions for Administrator, Operator and Viewer. Current evidence: [permissions](evidence/permission-v041-live.json), [deployment](evidence/permission-v041-deployment.json) and [preservation/account state](evidence/permission-v041-preservation.json). Historical v0.4 provider/backend evidence remains separate.

On **3 November 2026 (Hong Kong)**, the user-authorized test account `ee3070-staff` / `test@example.com` / **EE3070 Staff** was created through the controlled IAM bootstrap. Its **first test-account sign-in is verified**: the enabled `CONFIRMED` Cognito identity is linked through its immutable issuer/subject to an active Administrator account, and `/staff-api/session` returned the intended identity and permissions. Production managed code/PKCE sign-in, live account-panel loading, incorrect-password rejection, sign-out and provider re-prompt passed. Final provider/application readbacks confirmed exactly one enabled login and one active Administrator: the requested account. The three earlier synthetic accounts and temporary Operator/Viewer verification accounts are disabled in both stores. No real invitation email or SMS was sent. This reserved test address cannot receive recovery email; onboarding a real staff identity remains separate.

## Authentication paths

`GET /auth/config` is public and contains public Cognito configuration only. Staff API requests use `Authorization: Bearer <access token>` and the `/staff-api` prefix. For example, the staff form of `/api/exhibit-profiles` is `/staff-api/exhibit-profiles`. The HTTP API JWT authorizer verifies the token signature, issuer, intended client, lifetime and `reshow/staff` scope. Lambda additionally requires `token_use=access`, the expected issuer/client and the gateway-authorized staff route. ID tokens cannot authorize API calls.

The browser uses Cognito authorization-code flow with PKCE S256 and a public client with no secret. Request only the `reshow/staff` custom OAuth scope. This flow does not request OpenID Connect identity tokens; current user details come from `GET /staff-api/session`. State validation, one-use PKCE verifier storage, exact callback matching and safe internal return destinations remain browser responsibilities. Password entry and recovery belong to Cognito's managed pages.

Allowed callbacks are exactly `https://dkbznu9f5nh9n.cloudfront.net/auth/callback.html` and `http://localhost:8000/auth/callback.html`. Allowed logout destinations are the corresponding origin roots. The local origin is an explicit development exception, with no wildcard callback or CORS origin. A localhost client must select its exact configured callback from `allowed_callback_urls` rather than use the production `redirect_uri`.

The retained HTTP localhost callback was used by the isolated OAuth acceptance harness. The merged frontend's local dashboard/StudioAPI server now requires HTTPS, defaulting to `https://127.0.0.1:8000`; **local staff sign-in is unsupported** until that exact trusted HTTPS callback/logout origin is separately configured in Cognito, the browser allowlist and applicable API/media CORS. This release does not add those HTTPS local origins. Production CloudFront sign-in and the existing production/HTTP-harness allowlists are unchanged.

Access tokens last 15 minutes. Tokens remain in browser memory, with no persistent login. The client can start another code/PKCE flow using the provider's browser session when an access token expires; it must not loop on failure. Although Cognito can return a refresh token, the initial client does not persist or use it for automatic renewal. Clear memory on sign-out, identity changes and expiry. Short-lived state/verifier/return-path login transaction values may use session storage and must be removed after callback or failure; they are not API credentials.

`GET /staff-api/session` returns:

```json
{"user":{"staff_id":"staff-example","subject":"provider-subject","issuer":"configured-issuer","name":"Example staff","email":"staff@example.com","role":"Operator","account_status":"active","account_version":2,"permissions":{"view":true,"save_reports":true,"edit_exhibits":true,"device_commands":true,"manage_staff":false,"manage_accounts":false,"physical_cards":false,"submit_producer_evidence":false}}}
```

The authoritative website role and status live in the account record linked by issuer plus immutable Cognito subject. Directory email is not an identity key. The directory's existing role field remains metadata and does not grant website permission. Every request strongly reads the linked account; an old token cannot retain a demoted role or disabled account's access. There is no application authorization cache. Account first sign-in changes `invited` to `active`, with server-derived audit identity.

## Permission enforcement

| Operation | Administrator | Operator | Viewer |
| --- | --- | --- | --- |
| Read Overview/catalog/labels/activity and report previews/exports | Yes | Yes | Yes |
| Save snapshots, edit exhibits/images, drafts, assignment and lighting requests | Yes | Yes | No |
| Read exhibit scans | Yes | Yes | No |
| Read staff-card scans | Yes | No | No |
| Read operational workflow activity | Full authorized feed | Exhibition events only | Exhibition events only |
| Read non-card command state | Yes | Yes | Yes |
| Read card-command state and sensitive recovery details | Yes | No | No |
| Read or edit staff directory/accounts, access logs, physical-card requests | Yes | No | No |
| Upload device observations, scans, completed intervals, coverage or receipts | No | No | No |

All producer-write routes reject human credentials, including Administrators. Operator card command creation and card recovery requests are rejected; card commands are excluded from non-Administrator command listings and exact reads are forbidden. Unknown actions are denied. Current permissions are independent of physical-card permission: disabling login does not silently revoke a card, and card revocation does not disable login.

### Role-filtered reads in source 0.4.1

- `GET /staff-api/tag-scans` retains its required `device_id` and optional date/page filters, and adds optional `scan_type=exhibit|staff_card`. An Operator receives only known public exhibit-scan fields even when this filter is omitted. Explicit `scan_type=staff_card` and exact staff-card reads return 403 for Operators. Viewers cannot list or retrieve scans. Administrators may read either category. Missing, manipulated or unsupported filters never widen the caller's role.
- `GET /staff-api/workflow-activity` gives Operators and Viewers only `assignment`, `label` and `lighting` events with status `pending`, `acknowledged`, `failed`, `recovery_pending` or `recovered`. A linked command must have the matching allowed type; disguised card events are suppressed. The response field allowlist is `activity_id`, `type`, `status`, `device_id`, `exhibit_id`, `command_id`, `version`, `created_at` and `updated_at`. Actor, identity, free-text message, staff/card/account/access details and unknown fields are omitted. Administrators retain their full authorized feed. Optional list filter `type=card` is forbidden to non-admin callers. `GET /staff-api/workflow-activity/{activity_id}` applies the same projection and linked-command check; a restricted record returns 403 and a missing record returns 404. Exact reads reject query parameters rather than accepting a filter bypass.
- Command lists and exact reads allow non-admin users only known `assignment`, `label` and `lighting` records. Card and unknown command types cannot be retrieved by exact ID or filters. Non-admin output uses the documented `OperationalCommand` projection: it omits `actor`, original `request`, `lock_id`, free-text `failure_reason` and the recovery request/reason. Assignment profiles, label content and recovery metadata are individually allowlisted, so nested unknown fields cannot restore sensitive data. Operator creation/recovery responses use the same projection; Viewer mutations remain forbidden.

Filtering occurs on the backend before serialization. These are bounded pages rather than a total count: a page may have no permitted items while `next_cursor` remains non-null. Continue until null; no restricted-record totals are returned. Unknown or malformed filters return 400. The operational feed remains request/receipt provenance; it does not replace environmental/proximity analytics or change their completeness rules.

Operator scan cursors and non-admin command/activity cursors are authenticated, encrypted continuations. They are bound to the immutable account identity, current role/account version, collection and normalized filters, and expire after 15 minutes. Altered, expired, old-format or differently scoped cursors return 400; unavailable private-cursor key configuration returns 503. Restart at the first page after a filter/account change instead of editing or reusing a cursor. Administrator and producer cursor behavior is unchanged. The server-only key has no HTTP bootstrap route and is never returned in API metadata.

The IAM-only `scripts/prepare_staff_cursor_key.py` checks readiness without writing by default. `--provision` conditionally seeds one independent key after verifying the expected account/stack; an unchanged retry preserves an existing valid key and returns no key material. Provisioning and retry were verified on 3 November 2026. Run a readiness check before the guarded deployment; do not rotate the key as part of an ordinary package update. Runtime dependency/build commands are in the README.

Human mutations reject a supplied `actor`. Commands and recovery requests receive the validated `staff_id:subject` actor; workflow transactions additionally store an immutable staff audit entry containing issuer, subject, staff ID, current role, method and path. Account lifecycle transactions have their own structured audit records. No audit stores a bearer token, password or request body.

## Account administration

These routes require an active Administrator:

| Method and route | Input/meaning |
| --- | --- |
| `GET /staff-api/accounts?limit=20&cursor=...` | Bounded metadata pages; follow `next_cursor` through empty pages. |
| `GET /staff-api/accounts/{staff_id}` | Account metadata or 404 for a directory-only record. |
| `POST /staff-api/accounts/invitations` | `{ "invitation_id":"invite-example", "staff_id":"staff-example", "role":"Viewer", "delivery":"suppress" }`. Staff directory record must already exist. `delivery=email` explicitly requests a real invitation email; default is `suppress`. |
| `PATCH /staff-api/accounts/{staff_id}` | `{ "role":"Operator", "status":"disabled" }`, either field optional; send current positive integer `If-Match`. Status choices are `active` or `disabled`. |
| `POST /staff-api/accounts/{staff_id}/resend-invitation` | `{ "request_id":"resend-example" }` requests a provider invitation resend for an invited account. This action sends email. |

Invitation state progresses `provisioning` → `invited` → `active`; `disabled` blocks application access. A directory record may have no account at all. Invitation emails and temporary passwords are issued by Cognito, not returned by this API. The pool disables public sign-up. Initial passwords expire after seven days and must be changed on first sign-in. Optional software-token MFA is enabled at the pool; Administrators should enroll an authenticator through an agreed provider account-management workflow. No SMS dependency is enabled. Provider password recovery uses verified email and public-facing generic errors.

Invitations reserve a deterministic username and immutable invitation request before provider creation. An unchanged retry recovers from provider success followed by database failure without creating another identity or sending the initial email again. Changed payload under the same invitation ID returns 409. The Cognito subject is linked only from that reserved username, never by arbitrary email matching. Enrollment email is Administrator asserted and marked verified for provider recovery; choose and check it before invitation.

Disable/restore and role changes update application state first; a provider enable/disable operation is additional defense. A provider failure can therefore return an error after the application state changed. Read the account and retry using its new version; do not assume an HTTP failure means no change occurred. Similarly, resending reserves its request ID before asking Cognito: an ambiguous timeout is not proof that email was not sent. A repeated request ID does not send another email; an Administrator must inspect and deliberately choose a new request ID if another resend is required.

Last-active-Administrator protection uses a shared versioned administrator-count row in the same transaction as account changes. Concurrent demotions/disables cannot remove every active Administrator. Caller account versions are also checked during privileged account transactions to prevent a concurrent demotion retaining account-administration power. Invitation and card records are preserved.

## Sign-out and shared computers

Send `POST /staff-api/session/logout` with `{}` before clearing the in-memory access token. The application records an account-wide token issue-time cutoff and rejects existing tokens immediately, then attempts Cognito global sign-out. Response fields distinguish `application_sessions_revoked` and `provider_revoked`. Regardless of provider outcome, clear browser state and navigate through Cognito's logout endpoint with the configured client ID and exact logout URI to end its browser cookie session. Global sign-out alone does not end that browser cookie.

Application logout signs out the account's existing tokens across tabs/devices, not only the current tab. Tabs should clear private state through a transient browser coordination message; do not put tokens in that message or persistent storage. A fresh sign-in in the same second as the cutoff may be rejected until the next second. Refresh revocation by itself would not immediately invalidate gateway JWTs; the strong account cutoff supplies immediate application rejection. Gateway cryptographic validity can remain until the 15-minute expiry.

## Shared-token migration

The deployed `AUTH_ENFORCED=true` setting narrows the existing `/api` shared credential to an explicit **demo producer**. It cannot read staff/account directories or mutate management profiles, staff/cards, media or saved snapshots. It can upload/read legacy immutable demo records and request demo analytics, and discover/acknowledge commands for configured demo devices. Demo evidence must use `source=demo`.

Reserved default demo device prefixes: `esp32-demo-`, `ec2-demo-`, `analytics-`, `wf-`, `v04-`, `demo-`. Reserved demo exhibit prefixes: `analytics-`, `wf-`, `v04-`, `demo-`. The original upload-only and GET-only report scripts continue using the private demo credential and these fixtures. Existing stored records are preserved. Protected human data cannot be recovered through shared-token analytics: producer timelines exclude non-demo records before JSON/CSV aggregation. No producer credential belongs in the frontend bundle.

## Hardware producer authentication

Hardware uses `Authorization: Bearer <hardware token>` for `POST /api/device-status` and `POST /api/readings`. Set `source` to `"device"` and `device_id` to the token's exact registered device ID. A showcase must be registered to the device before its first status upload. Each reading must reference an existing legacy exhibit through `exhibit_id`. Authentication verifies the credential that submitted the record. Physical observations must come from the hardware.

Use the POST response as the producer receipt. Repeat the same ID and payload for safe retries. For independent readback, use an authorized staff session with `GET /staff-api/device-status/{status_id}` or `GET /staff-api/readings/{reading_id}`. The hardware credential grants no read or other protected operation. Missing, invalid and revoked credentials return 401. Valid hardware credentials used outside their route or body scope return 403.

An IAM-authorized operator registers the showcase and provisions the token with `scripts/manage_hardware_device.py enroll`. Choose a private local directory (mode `0700`) for the credential file:

```sh
python3 scripts/manage_hardware_device.py enroll \
  --device-id reshow-mega-01 \
  --showcase-id showcase-reshow-mega-01 \
  --showcase-name 'ReShow Mega 01' \
  --credential-file /absolute/private/reshow-mega-01.json
```

Keep the file (mode `0600`) in the producer's private configuration and out of logs, source control and browser code. The API looks up the token's SHA-256 digest with a strongly consistent read on every request. Run `scripts/manage_hardware_device.py revoke --device-id reshow-mega-01` to revoke the current enrollment. Add `--credential-file` to target the token in that file. Use a fresh credential file for re-enrollment after revocation.

## Controlled first Administrator and synthetic cleanup

There is no HTTP bootstrap endpoint. The IAM-authorized local script `scripts/manage_staff_account.py` verifies the AWS account and reads the existing stack configuration. `prepare` makes no provider or application change:

```sh
python3 scripts/manage_staff_account.py prepare --staff-id staff-example --email staff@example.com --name "Example administrator"
```

After explicit authorization for the chosen account, `bootstrap --confirm-aws-controlled-bootstrap` creates the directory, Cognito identity and authoritative Administrator account. Default `--delivery suppress` sends no invitation email; `--delivery email` explicitly requests it. The script never creates, returns, prints or stores a password. Synthetic provider testing may use an IAM-controlled password-setting command in a separate protected test harness; passwords and tokens must stay outside deliverables/logs.

Synthetic bootstrap requires `--synthetic`, an `@example.com` enrollment and suppressed delivery. `retire-synthetic --staff-id ... --confirm-aws-controlled-bootstrap` disables that explicitly synthetic identity while preserving historical records. Only this IAM-controlled cleanup may bring the application administrator count to zero after synthetic testing; the HTTP last-Administrator guard always remains enforced. A subsequent real first Administrator still requires this AWS-controlled process and separate invitation authorization. No real invitation has been implied by preparing the release.

Production email volume uses Cognito's configured delivery service and its quotas; no custom SES identity or paid SMS has been provisioned by this implementation. Software MFA enrollment/recovery and real-user invitation delivery require an agreed operational handoff and provider verification.

## Verification boundaries

The focused `tests/test_auth.py` suite covers current-role/disable/logout enforcement, wrong token purpose/client/issuer/time/scope and gateway binding, direct permission denials, shared-token bypass rejection, synthetic namespaces/source, immutable-subject sessions, invitation partial retry, last-Administrator concurrency and audit identity. Gateway signature validation, managed first sign-in, cancellation/password recovery/invitation expiry and browser PKCE/logout require actual AWS/provider/browser tests. A fabricated requestContext test is not evidence of live JWT validation.

The 0.4.1 unified source suite passed 157 tests, with overlapping independent focused and mutation checks. Live direct API checks passed the exhibit-scan and safe-activity rules for all three roles; non-admin command/card/recovery and sensitive-field denials; empty filtered pages and cursor continuation/tampering/scope rules; missing/invalid credentials; immediate Operator role-change/disable enforcement; last-Administrator protection; and human/shared-producer restrictions. Both temporary Operator/Viewer accounts were then disabled through the live Administrator API; final provider/application readbacks are in preservation evidence. These are synthetic engineering checks, not hardware evidence. No real invitation email/SMS, real card permission or physical device operation was used for acceptance.

## Completed deployment and account state — 27 October 2026

The final coordinated frontend/StudioAPI release is `fad8f52870836d592f96c2fe38b48fb1647bda1f`. Static/API commits match and the gated checks/Sonar/AI/deploy workflow passed. Production Cognito acceptance used two independent Administrator browser tabs: live account table, removed callback query, visible local-demo separation, cross-tab sign-out, old independent JWT 401 before retirement, provider username/password re-prompt and zero console warnings/errors all passed. See `evidence/v04-frontend-browser-live.json` and `v04-coordinated-frontend-deployment.json`.

At 15:08 Hong Kong on 27 October, all three then-existing synthetic Cognito identities were disabled and their application accounts strongly read as disabled, with history retained. The active Administrator count was then 0 and HTTP bootstrap remained closed (`evidence/v04-synthetic-cleanup.json`). This is historical cleanup evidence; the current authorized test Administrator is recorded above. No real invitation email or SMS was sent. First temporary-password change, real delivery/recovery/invitation expiry and MFA operations remain unverified. Local HTTPS staff login remains unsupported until separately configured as stated above.
