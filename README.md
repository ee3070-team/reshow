# ReShow

A six-tab desktop dashboard for a reusable smart exhibition showcase. Built with HTML, CSS and plain JavaScript ES modules, without a build step or framework. Choose an explicitly local **Demo** workspace or **Live** staff authentication against the existing backend. The frontend never submits device evidence or acknowledges live commands.

## Run locally

Open PowerShell in this project directory and run:

```powershell
python -m http.server 8000 --bind 127.0.0.1
```

Open **http://localhost:8000** in a desktop browser. Live login requires this exact origin and port because its callback is allowlisted; 127.0.0.1 or another port works only for Demo mode. Use a static HTTP server rather than opening `index.html` directly: browsers restrict ES modules on `file:` URLs. Press Ctrl+C in the server terminal to stop it.

Use the same origin/port to keep saved demo records. Demo data is stored in that browser's `localStorage`; it is not shared with a server. Live records remain on the backend. If storage is unavailable, an on-screen warning explains that changes are held in memory.

## Live staff sign in and integration

The public contract and `/auth/config` were fetched on **31 October 2026 HKT** and still identify API **0.4.0**. The API origin is configured in the `reshow-api-origin` meta tag in `index.html` and `auth/callback.html`. It contains no secret. [Current API documentation](https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/docs). Configuration is fetched at runtime; provider endpoints and callback allowlists are not guessed.

Choose **Live** on the sign-in screen, then **Continue to live sign in**. Passwords and recovery are handled by the provider. The browser uses authorization code + PKCE/S256; the callback checks a short-lived state/verifier transaction, removes callback parameters, and checks `/staff-api/session`. Only the access token is used, in memory, for `/staff-api` requests. The current access lifetime is 900 seconds. Reload requires sign-in again through the provider session; there is no background refresh loop or persistent live token. Sign out first invalidates the account's existing sessions on the backend, then opens provider logout. A failed logout is surfaced and is not reported as successful.

Live identities and effective permissions are rechecked on focus and every 30 seconds. Backend permission checks remain authoritative. Viewers may preview, read saved reports and export; only permitted Operators/Administrators save snapshots. Staff directory metadata, website account roles/status, and physical card permissions have separate controls. Account preparation explicitly uses `delivery: suppress`; **this build has no send/resend invitation email action**. No invitation or live management mutation was performed during implementation.

All six tabs use the separate live data adapter. Requests are paced, concurrent duplicate reads are coalesced, and empty filtered pages with `next_cursor` are followed. HTTP errors stay visible; live failures never switch to demo data. Profile/draft edits retain their opened versions using `If-Match`. Conflicts preserve editor content for review. Unsaved text from exhibit/label editors is shown for copying when a live session expires; copy it before navigating back through provider login. It is not persisted.

**Image upload is deferred.** Text-only new records send null optional media references. Existing remote references survive text edits. Selected local images are labelled **Preview only—not uploaded** and are excluded from live writes. Existing authorized image downloads may be displayed; a media error does not block text workflows.

**Device evidence:** pending, failed, recovery-pending and recovered commands remain distinct from acknowledged state. Source `demo` received from the live backend stays labelled synthetic; `device` is a producer assertion, not independent hardware verification. Readings and contact freshness are separate. No remote unlock, producer ingestion or acknowledgement route is exposed.

**Analytics:** inclusive HKT date filters become UTC requests, limited to 31 calendar days with hourly buckets (at most 744). Backend partial/unknown/ambiguous/out-of-scope/future states and coverage are retained. Current-day results show the backend's effective cutoff. Overview, Activity and report previews share normalization. Saving a preview creates a new server capture, which may differ; exports and printing use the displayed saved snapshot once opened. Warnings count distinct source IDs, not attribution fragments.

Known backend-dependent limits:

- Historical access attempts identify the card, without inferring its past owner from current staff assignments.
- Tag-scan reads remain Administrator-only. Operators may select existing profiles for confirmed assignment; scan controls explain the restriction.
- Workflow audit reads remain Administrator-only. Other roles retain permitted analytics and see an availability notice.
- Command reads are documented for all roles. The frontend hides card commands from non-admin displays; **server-side card-detail redaction remains unverified** and cannot be guaranteed by the UI.
- Staff-card enrollment requires a new hardware scan with the displayed attempt nonce. A producer must coordinate that nonce; the browser does not start hardware or invent scans.
- Expired/recovery-pending commands need backend/producer recovery. The UI does not release locks or fake reconciliation.
- Interactive Cognito login, authenticated CORS/data access, real permissions and hardware outcomes require an explicitly approved account/environment for end-to-end verification.

Public CORS OPTIONS was checked without credentials or writes: localhost:8000 is allowed with GET/POST/PATCH/PUT and Authorization/Content-Type/If-Match. This differs from stale `x-cors` metadata in OpenAPI; actual authenticated integration is still unverified.

## Demo staff sign in

The Demo workspace uses an email-and-password sample login screen. Expand **Demo accounts** to select a sample email. All demo staff use the public password **`DemoOnly!3070`**; never enter a personal password.

| Role | Sample email | Available actions |
| --- | --- | --- |
| Admin (Administrator) | `sarah.chen@reshow.demo` | All dashboard actions, Staff Access, cards and demo controls |
| Operator | `james.wong@reshow.demo` | Edit exhibits, labels and lighting; browse activity and generate/export reports |
| Viewer | `emily.lau@reshow.demo` | Read-only dashboard, exhibits and labels; browse activity and generate/export reports |

All six tabs remain visible; Staff access explains its Administrator requirement for other roles. Adding a staff profile enables its demo login with the shared password; changing its email or role changes its login details or permissions. Website login is independent of physical card access. Administrators cannot demote themselves or remove the last administrator role.

**Sign out** clears the session and prompts before discarding unsaved edits. Sessions expire after eight hours of real time and survive refresh in the same tab through `sessionStorage`. If session storage is blocked, sign-in works in memory until reload. The exhibition demo clock does not affect expiry.

This is a browser-only simulation, not a security boundary. No passwords are saved. The separate Live workspace uses backend authentication and permissions.

## Explore the six tabs

- **Overview:** confirmed showcase status, environmental readings, assigned exhibit, actual acknowledged label, lighting requests, heatmap, and recent events.
- **Exhibits:** search/filter, create/edit profiles and limits, wait for an authorized hardware scan (or simulate in Demo), confirm assignment, and inspect illustrative model metadata.
- **Labels:** edit the live monochrome preview, choose a local image, save a draft, and separately request delivery. The previously acknowledged label remains visible until delivery succeeds.
- **Activity:** filter by Hong Kong dates and exhibit, switch heatmap measures, inspect cells with mouse or keyboard, and filter the event log.
- **Reports:** choose sections and filters, generate a preview, separately save a live server snapshot, export its CSV, or use **Print / Save as PDF**. In a regular browser, choose a PDF destination in the print dialog. This is browser printing, not a direct PDF download.
- **Staff access:** administrators manage staff and separate website accounts, register scanned cards, confirm revoke/restore, and filter historical accepted/denied attempts. Website roles control permitted UI and service actions, separately from physical card permissions.

There is no Settings tab or remote unlock action. Layouts target desktop widths of 1280, 1440 and 1920 pixels. The small exhibit illustrations are local editable SVG assets; model previews are metadata only.

## Demo controls

The bottom-left **Demo controls** button opens a development dialog:

| Scenario | Behaviour |
| --- | --- |
| Normal | Commands acknowledge after about 1.3 seconds. |
| Loading | Service reads take about 1.8 seconds each; loading feedback remains visible. |
| Empty | No records or readings are displayed; existing stored edits are preserved. |
| Offline | Retained readings are stale. Label sending is disabled; other requested device commands fail without changing confirmed state. |
| Pending | Commands remain pending until **Acknowledge pending** is clicked. Switching back to Normal alone does not settle already held commands. |
| Failed | A command shows pending, then fails while confirmed device state stays unchanged. |

**Reset demo records** restores the seed after confirmation and removes demo edits/reports. Reloading with a pending command marks it failed because delivery cannot be established across reloads.

The fixed demonstration clock starts on **6 October 2026 at 13:20 HKT**, matching the reference exhibition, and advances deterministically during actions. It is deliberately separate from the computer's current date. All displayed timestamps use Hong Kong time. Activity covers exhibition hours 09:00–17:00 on 1–6 October. Incomplete and future hours are excluded from totals; missing observations are never counted as zero. CSV timestamps are explicitly labelled UTC and retain full precision. Duration cards display whole minutes.

## Source map

```text
index.html              App shell and six-tab navigation
auth/callback.html      OAuth callback shell (root base URL)
js/auth.js              PKCE, memory token, backend session and logout
js/api.js               Explicit Demo / Live service facade
js/live-api.js          Staff API adapter and live record normalization
css/styles.css          Shared desktop components and print rules
css/exhibits.css         Exhibit and label layouts
css/staff.css            Staff directory and card registration layout
css/login.css            Sign-in screen and session visibility
js/app.js               Routing, login/session guards, demo dialog
js/login.js             Email/password form and public sample accounts
js/ui.js                Icons, safe text escaping, dates, dialogs, heatmaps
js/data.js              Seed records, demo clock and storage version
js/services.js          Async data access, validation and acknowledgements
js/pages/               One UI module per tab
assets/                 Local SVG illustrations
```

The facade in `js/api.js` selects existing demo services or `js/live-api.js`. `js/auth.js` implements provider login/session handling, and `auth/callback.html` loads the same app shell with a root base URL. Keep callback shell markup aligned when changing `index.html`. Telemetry, command execution, durable evidence, permissions and hardware integration remain backend/producer responsibilities.

The authentication boundary in `js/services.js` exposes async `signIn(email, password)`, `signOut()`, and `getSession()`, plus synchronous `can(permission)` for the UI. A session read returns `{ id, name, email, role }` or `null`. Permission keys are `view`, `operate`, `staff`, `demo`, and `reports`. Client permission checks guide the interface; a real API must independently authorize every request.

## Files needed to run

Serve `index.html` together with the complete `auth/`, `css/`, `js/` and `assets/` folders, preserving their relative paths. No build step or package installation is required.

The README and `.gitignore` support repository use but are not runtime dependencies. Local supporting material is grouped in `project-materials/`, which is ignored by Git. This includes design references, agent instructions, implementation and backend handoff notes, tests, verification screenshots, and the weekly report with its working files.

If the local supporting material is available, run its tests from the project root:

```powershell
node --test project-materials/tests/*.test.mjs
```


## Verification (31 October 2026)

Run `node --test project-materials/tests/*.test.mjs`. Tests use isolated in-memory fixtures and cover demo state, auth/PKCE, API pagination/versioning, permissions, media preservation, report snapshots and rendering. Supporting tests and contract captures remain Git-ignored under the existing repository policy. The optional local contract fixture at `/project-materials/verification/live-fixture.html` imports actual live adapter/page code but uses mock responses only; it is deliberately separate from the app and should not be published.

Six-tab browser verification and detailed limitations are recorded in `project-materials/VERIFICATION.md`. CSV download was checked. The embedded browser stalls on native print; use a regular browser's **Print / Save as PDF** dialog. PDF output/pagination has not been verified. No deployed data was changed, invitation sent, push made or deployment performed.

## Automatic Live refresh (4 November 2026)

Live Overview refreshes automatically. Each cycle reads the selected showcase's exact detail and command state, waits for subscribed refresh work to finish, then waits five seconds before the next cycle. Activity refreshes even when showcase IDs, timestamps and command versions are unchanged. Reads remain paced and concurrent duplicates are shared; slow requests do not stack polling cycles.

Overview paints readings and confirmed exhibit/label/lighting before waiting for activity. A failed refresh keeps displayed records with a visible retry notice; retained activity is labelled potentially stale. Pending requests still do not replace acknowledged device state. Existing editor dirty-state/version guards remain in place.

Verification: 98 local tests pass, including 18 new refresh regressions. Browser checks use isolated changing mock responses; authenticated read-only Overview also showed advancing contact age and activity cutoff without reload. No external producer measurement change or physical outcome was observed. The public contract fetched on 4 November is version 0.4.1; older integration notes above describe the earlier 0.4.0 audit. Detailed evidence and backend follow-up: `project-materials/LIVE_REFRESH_VERIFICATION.md` (local supporting material, intentionally Git-ignored). No live records were changed, pushed or deployed.

## Faster page navigation (4 November 2026 follow-up)

Live pages fetch only the collections they use. The shell no longer fetches the entire workspace before opening a page, and pages reuse included profile/staff/report lists. Request pacing and live refresh remain enabled; this optimization adds no persistent data cache.

A local Administrator Overview benchmark with the production 550 ms pacing reduced 17 reads to 7: readings became ready in 1.69 s instead of 7.29 s, and activity in 3.37 s instead of 8.98 s. These are mock timings without network latency, not a Live service guarantee. Run `node project-materials/verification/loading-benchmark.mjs` to reproduce. All 114 tests pass. Details: `project-materials/PAGE_LOADING_VERIFICATION.md`.

## Authenticated loading follow-up (4 November 2026)

Actual Live navigation was measured after the user reported little improvement. A mixed JavaScript module cache was observed in the test browser; the main and callback shells now use matching versioned import maps so an updated page loads a consistent module set. After updating source, bump the module revision in **both** shells. Reload the site to receive the current map; Ctrl+Shift+R forces a full reload in Chrome. Live sign-in may be required again.

Measured authenticated page completion with the updated modules ranged from about 2.0 to 4.7 seconds in this session. Individual API roundtrips normally took 0.6–0.8 seconds, so Live is slower than the earlier network-free mock benchmark. The loading indicator now remains visible throughout page fetching. See `project-materials/LIVE_LOADING_VERIFICATION.md` for per-page timings, conditions and validation. The current module revision is `20261104-liveperf-2`; no deployment was performed.
