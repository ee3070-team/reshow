# Fix ReShow live-record loading: HTTP 429 on CORS preflight

You are the backend teammate working on the existing ReShow AWS service. Investigate and fix the live-record loading failure below. Inspect the deployed configuration and logs, identify the actual cause, implement a targeted fix in the backend repository/infrastructure configuration, and verify the result. Do not stop at a proposed explanation.

## Context and boundaries

- API origin: `https://urauw4ckw1.execute-api.us-east-1.amazonaws.com`
- API documentation: `https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/docs`
- Local frontend: `http://localhost:8000/#labels`
- Configured hosted frontend: `https://dkbznu9f5nh9n.cloudfront.net/`
- The frontend uses Cognito authorization code + PKCE and an in-memory access token for `/staff-api` requests. A real Administrator session was visibly active during reproduction.
- Read applicable backend repository instructions and the current API contract. Older frontend handoff documents may describe superseded milestones; this incident was verified on **3 November 2026 HKT**.
- Preserve existing records, permissions, device command/acknowledgement semantics, and unrelated changes. Do not reset data or relax authentication to make reads succeed.
- Obtain test credentials through the existing secure channel. Do not put passwords, access tokens, authorization codes, or cookies in source, this handoff, or logs.
- Follow the existing deployment authorization. Prepare and test the fix; deploy only where already authorized. Clearly distinguish local tests from verification of a deployed fix.

## Verified observations

The signed-in Labels page displayed:

> Could not load live records
>
> Cannot reach the live service. Check your connection and retry.

Its session-refresh banner also displayed `Too Many Requests`. Clicking **Try again** reproduced the failure while the Administrator identity remained visible.

Independent HTTP probes from the same computer produced the following results. These probes did **not** include an access token. OPTIONS is the browser preflight; the unauthenticated GET was a comparison probe, not a test of authorized record access.

| HKT time, 3 Nov 2026 | Method and route | Status | API Gateway request ID |
| --- | --- | --- | --- |
| 13:59:10 | OPTIONS `/staff-api/showcases?limit=100` | 429 | `GP-3VhQQoAMESWg=` |
| 13:59:44 | OPTIONS `/staff-api/exhibit-profiles?limit=100` | 429 | `GP-8ujySIAMEa4Q=` |
| 13:59:48 | GET `/staff-api/showcases?limit=100`, without Authorization | 401 | `GP-9SgM2IAMESGQ=` |
| 14:00:07 | OPTIONS `/staff-api/session` | 204 | `GP_AOg3UIAMESXQ=` |
| 14:00:17 | OPTIONS `/staff-api/showcases?limit=100` | 429 | `GP_Byhp_oAMEadA=` |

For CloudWatch correlation, these correspond to **05:59:10–06:00:17 UTC**.

The failed preflights returned:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
access-control-allow-origin: http://localhost:8000
access-control-allow-methods: GET,OPTIONS,PATCH,POST,PUT
access-control-allow-headers: authorization,content-type,if-match
vary: origin
access-control-max-age: 600

{"message":"Too Many Requests"}
```

The successful session preflight returned 204 with the same allowed origin/method/header values. `/auth/config` also returned 200 earlier in the investigation.

**Established failure:** record-route preflights were rejected with 429, even though CORS headers were present. A non-successful preflight blocks the browser's subsequent authenticated request, consistent with the generic connection error shown by the frontend.

**Not yet established:** which infrastructure/application layer generated the 429, the configured rate/burst limits, the traffic responsible, or whether authorized record handlers have additional faults. We did not inspect AWS logs/configuration or capture the signed-in browser's network responses. Do not assume a particular throttle setting is the root cause solely from this evidence.

## Reproduce safely

Run these individually at a low request rate. Use `curl.exe` in Windows PowerShell. No token is needed for OPTIONS.

```powershell
curl.exe -i --max-time 20 -X OPTIONS 'https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/staff-api/showcases?limit=100' -H 'Origin: http://localhost:8000' -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: authorization'

curl.exe -i --max-time 20 -X OPTIONS 'https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/staff-api/exhibit-profiles?limit=100' -H 'Origin: http://localhost:8000' -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: authorization'

curl.exe -i --max-time 20 -X OPTIONS 'https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/staff-api/session' -H 'Origin: http://localhost:8000' -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: authorization'
```

Repeat the relevant preflight checks using the configured hosted frontend origin as well. Confirm current allowed origins from configuration rather than expanding the allowlist speculatively.

## Investigation and implementation

1. Identify the deployed API type, stage, route matching, OPTIONS handling, authorizer configuration, and integration targets. Compare `/staff-api/session` with the record routes and any catch-all route.
2. Correlate the request IDs and timestamps with available API Gateway access/execution logs, integration logs, and metrics. Determine whether rejection occurs at API Gateway, an applicable edge/rate-limit layer, application middleware, or integration capacity. Inspect only layers actually present in this deployment.
3. Check effective stage/default and per-route throttles, rate and burst settings, shared quotas, and configuration drift. Inspect usage plans/API keys only if applicable to this API type. Check integration throttling/concurrency if logs implicate it. Do not simply raise every limit or disable protections.
4. Ensure allowed-origin preflight is handled successfully without requiring a bearer token, and that normal browser startup and navigation fit the intended request budget. Account for preflight traffic as well as actual API requests. Keep access control on actual staff requests intact.
5. Preserve appropriate CORS headers on success and error responses, including 401, 403, 429 and 5xx where controlled by this service. An allowed-origin header alone does not make a 429 preflight valid. Preserve required methods and `Authorization`, `Content-Type`, and `If-Match` support. Preserve existing exposed response headers.
6. For rate limits controlled by the application, provide a meaningful retry interval where possible. If `Retry-After` is returned, expose it through CORS so browser JavaScript can read it. Document any gateway-generated response limitations. Do not claim the browser can read the status/body of a failed preflight as a normal fetch response.
7. Encode the fix in the repository's normal deployment/infrastructure configuration, with focused regression checks. Explain any necessary configuration-only remediation and how drift will be prevented.

## Frontend traffic to account for

Source inspection found these contributors; their exact share of live traffic was not measured:

- `js/live-api.js` polls showcase/command data every five seconds while subscribed and authorized.
- On poll failure, it emits a change event. The app subscriber then requests a complete snapshot, creating additional load during an outage.
- A full Administrator snapshot can read showcases, exhibits, commands, workflow activity, staff, and reports, with pagination and optional media reads adding requests.
- Adapter requests have a 550 ms minimum start interval and concurrent identical-read coalescing. GET requests receiving a visible 429 retry twice, after approximately 1.1 and 2.2 seconds. This does not provide a shared server-directed cooldown.
- `js/app.js` checks the session on window focus and every 30 seconds. These auth requests bypass the live adapter's pacing.

Include this workload when choosing reasonable limits. Coordinate a separate frontend change to stop error-triggered request cascades, apply shared cooldown/backoff, and improve error wording. Backend preflight handling must still work correctly; do not present a frontend-only mitigation as a verified backend fix. Do not automatically retry mutations whose outcome is uncertain.

## Acceptance checks

- At normal traffic levels, OPTIONS succeeds for the exact failing record URLs, the session route, and other staff endpoints used by the six tabs, from both configured frontend origins. Verify the actual allow-method/header values needed by each request.
- A valid Administrator can sign in and load live Labels and Exhibits through the browser without the reproduced preflight failure. Check Overview, Activity, Reports, and Staff access for related failures. Use read-only workflows and existing records.
- Observe an idle session and normal tab navigation for at least two minutes, covering several five-second polls and 30-second session checks. Record response statuses and request volume; check for repeated 429s, failed preflights, or request cascades.
- Missing/invalid credentials still fail on protected actual requests. Viewer/Operator/Administrator permissions remain enforced server-side. Do not broaden allowed origins or access roles to pass the test.
- Validate deliberate throttling and recovery in an isolated test environment with a bounded workload, not an uncontrolled production load test. Confirm useful errors/retry guidance and unchanged mutation safety.
- No exhibit edits, invitations, account changes, device commands, or fabricated acknowledgements are required to verify this incident. Use fixtures for tests needing mutations.

## Return a concise handoff

Report:

1. The confirmed cause, with supporting log/configuration evidence.
2. Files and effective infrastructure settings changed, including relevant before/after values.
3. Tests run and results; environment/version deployed if applicable.
4. Before/after HTTP statuses for the failing preflights and authenticated read/browser results, with timestamps and sanitized request IDs.
5. Any remaining frontend work, deployment step, or unresolved dependency, plus a rollback procedure for the configuration change.

Never label the incident fixed based only on a successful `/auth/config`, session response, or unit test. Verify actual record loading after the remediation, or explicitly state what remains unverified.
