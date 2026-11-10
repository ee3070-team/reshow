# ReShow

A desktop dashboard for a reusable smart exhibition showcase, with six core workspace tabs plus Live showcase and 3D & AI studio views. Built with HTML, CSS and plain JavaScript ES modules, without a frontend build step or framework. Choose an explicitly local **Demo** workspace or **Live** staff authentication against the existing backend. The frontend never submits device evidence or acknowledges live commands. A standard-library Python HTTPS server supports local use; production serves static files through CloudFront and routes the studio API to a separate Python Lambda function. The 3D studio uses the included Three.js r128 and GLTFLoader files.

## Run locally

Use Python 3.14 or later. Open PowerShell in this project directory and run:

```powershell
python server.py --cert-file path/to/trusted-local-cert.pem --key-file path/to/local-key.pem
```

Stop any previously running local web server first, then open **https://127.0.0.1:8000** in a desktop browser. If port 8000 is occupied by another application, use `python server.py --cert-file path/to/trusted-local-cert.pem --key-file path/to/local-key.pem --port 8001` and open **https://127.0.0.1:8001**. Use a certificate trusted by your browser whose subject alternative names include the chosen hostname (`127.0.0.1` or `localhost`). The server requires both TLS files and has no plaintext fallback. The server hosts the dashboard and its local showcase API together; do not open `index.html` directly because browsers restrict ES modules on `file:` URLs. Press Ctrl+C in the server terminal to stop it.

Staff sign-in is configured for the production website using the exact `/auth/callback.html` path. The previous HTTP localhost callback is retained in the account configuration, but the local server now requires HTTPS; a trusted HTTPS local callback must be explicitly configured in Cognito and the client allowlist before local staff sign-in can work. PKCE state/verifier expire after ten minutes in sessionStorage; access and refresh tokens remain in memory and are cleared on logout or expiry. Other demo origins can use the local showcase without staff sign-in.

Use the same origin/port to keep saved demo records. Demo data is stored in that browser's `localStorage`; it is not shared with a server. Live records remain on the backend. If storage is unavailable, an on-screen warning explains that changes are held in memory.

## Live staff sign in and integration

The public contract and `/auth/config` were fetched on **31 October 2026 HKT** and still identify API **0.4.0**. The API origin is configured in the `reshow-api-origin` meta tag in `index.html` and `auth/callback.html`. It contains no secret. [Current API documentation](https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/docs). Configuration is fetched at runtime; provider endpoints and callback allowlists are not guessed.

Choose **Live** on the sign-in screen, then **Continue to live sign in**. Passwords and recovery are handled by the provider. The browser uses authorization code + PKCE/S256; the callback checks a short-lived state/verifier transaction, removes callback parameters, and checks `/staff-api/session`. Only the access token is used, in memory, for `/staff-api` requests. The current access lifetime is 900 seconds. Reload requires sign-in again through the provider session; there is no background refresh loop or persistent live token. Sign out first invalidates the account's existing sessions on the backend, then opens provider logout. A failed logout is surfaced and is not reported as successful.

Live identities and effective permissions are rechecked on focus and every 30 seconds. Backend permission checks remain authoritative. Viewers may preview, read saved reports and export; only permitted Operators/Administrators save snapshots. Staff directory metadata, website account roles/status, and physical card permissions have separate controls. Account preparation explicitly uses `delivery: suppress`; **this build has no send/resend invitation email action**. No invitation or live management mutation was performed during implementation.

In Live mode, all six core workspace tabs use the separate live data adapter. Requests are paced, concurrent duplicate reads are coalesced, and empty filtered pages with `next_cursor` are followed. HTTP errors stay visible; live failures never switch to demo data. Profile/draft edits retain their opened versions using `If-Match`. Conflicts preserve editor content for review. Unsaved text from exhibit/label editors is shown for copying when a live session expires; copy it before navigating back through provider login. It is not persisted.

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

The Demo workspace is a local role simulation. Expand **Demo accounts**, select a sample email, then choose **Open demo**. No password is required or accepted. Use **Live** for authenticated staff access.

| Role | Sample email | Available actions |
| --- | --- | --- |
| Admin (Administrator) | `sarah.chen@reshow.demo` | All dashboard actions, Staff Access, cards and demo controls |
| Operator | `james.wong@reshow.demo` | Edit exhibits, labels and lighting; browse activity and generate/export reports |
| Viewer | `emily.lau@reshow.demo` | Read-only dashboard, exhibits and labels; browse activity and generate/export reports |

All six tabs remain visible; Staff access explains its Administrator requirement for other roles. Adding a staff profile enables its demo login without a password; changing its email or role changes its login details or permissions. Website login is independent of physical card access. Administrators cannot demote themselves or remove the last administrator role.

**Sign out** clears the session and prompts before discarding unsaved edits. Sessions expire after eight hours of real time and survive refresh in the same tab through `sessionStorage`. If session storage is blocked, sign-in works in memory until reload. The exhibition demo clock does not affect expiry.

This is a browser-only simulation, not a security boundary. No passwords are saved. The separate Live workspace uses backend authentication and permissions.

## Explore the dashboard

- **Overview:** confirmed showcase status, environmental readings, assigned exhibit, actual acknowledged label, lighting requests, heatmap, and recent events.
- **Exhibits:** search/filter, create/edit profiles and limits, wait for an authorized hardware scan (or simulate in Demo), confirm assignment, and inspect illustrative model metadata.
- **Labels:** edit the live monochrome preview, choose a local image, save a draft, and separately request delivery. The previously acknowledged label remains visible until delivery succeeds.
- **Activity:** filter by Hong Kong dates and exhibit, switch heatmap measures, inspect cells with mouse or keyboard, and filter the event log.
- **Reports:** choose sections and filters, generate a preview, separately save a live server snapshot, export its CSV, or use **Print / Save as PDF**. In a regular browser, choose a PDF destination in the print dialog. This is browser printing, not a direct PDF download.
- **Staff access:** administrators manage staff and separate website accounts, register scanned cards, confirm revoke/restore, and filter historical accepted/denied attempts. Website roles control permitted UI and service actions, separately from physical card permissions.
- **Live showcase:** sign in to inspect the shared AWS `demo-test-case` fixture, its actual latest readings, saved profiles/label drafts, confirmed device state, command history and historical report. Changes from the [Python data tools](tools/demo-showcase/README.md) appear automatically while this tab is visible: the next paced read starts 30 seconds after the previous one finishes. Loads never overlap. Hiding the tab, navigating away or signing out stops its requests. Automatic refresh can be paused; **Refresh live data** still performs a single check. The previous display remains visible while loading or after a transient error, with retained sections explicitly marked; a permission denial clears that section. Reads show one page of up to 20 entries per history section; role filtering can leave fewer visible entries. Administrator-only card/access data and Operator exhibit scans follow the backend's role rules. The six core workspace tabs use browser-local samples in Demo mode and the separate live adapter in Live mode.
- **3D & AI studio:** explore and adjust the 3D model, import a `.glb`, export four hologram views, and generate exhibit JSON from the included `assets/helmet.jpg` photo or another selected image. Locally this tab uses the same Python server as the rest of the site; the deployed site uses its same-origin HTTPS API.

Image-to-JSON generation requires Cloudflare Workers AI access. Enter your 32-character Cloudflare Account ID and API token in the **3D & AI studio** tab when generating. Credentials are sent to the Python API for that request only; they are not stored in browser storage or written to disk. Production forwards them through CloudFront HTTPS to Lambda without API caching or application request-body logging. Enable the model and accept any required model terms in Cloudflare before generating. Review the [model license](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/LICENSE) and [acceptable use policy](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/USE_POLICY.md) before using it. The image is processed in memory and is not saved.

There is no Settings tab or remote unlock action. Layouts target desktop widths of 1280, 1440 and 1920 pixels. Exhibit illustrations are local editable SVG assets; model metadata in the Exhibits tab is illustrative.

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
index.html              App shell, six workspace tabs and supplementary views
auth/callback.html      OAuth callback shell (root base URL)
js/auth-session.js      Shared PKCE, memory token, backend session and logout
js/session-auth.js      Dashboard adapter for the shared session
js/api.js               Explicit Demo / Live service facade
js/live-api.js          Staff API adapter and live record normalization
js/live-accounts.js     Live account controls backed by the staff API
js/live-showcase.js     Paced, read-only AWS showcase fixture loader
js/live-refresh.js      Visible-tab refresh scheduler with no overlapping loads
js/pages/live-demo.js   Signed-in live fixture view and manual refresh
server.py               Local Python server and shared Cloudflare API functions
lambda_handler.py       Production Lambda adapter for the showcase API
BACKEND_ASSET_UPLOAD.md  Backend contract proposal for exhibit .glb and .jpg assets
css/styles.css          Shared desktop components and print rules
css/exhibits.css         Exhibit and label layouts
css/staff.css            Staff directory and card registration layout
css/login.css            Sign-in screen and session visibility
js/app.js               Routing, login/session guards, demo dialog
js/login.js             Demo email selection and provider-backed Live sign-in
js/ui.js                Icons, safe text escaping, dates, dialogs, heatmaps
js/data.js              Seed records, demo clock and storage version
js/services.js          Async data access, validation and acknowledgements
js/pages/               One UI module per tab
js/pages/technology-showcase.js  3D studio and image-to-JSON generator
css/technology-showcase.css      Showcase-tab components
js/vendor/              Included Three.js r128 and GLTFLoader
assets/                 Local SVG illustrations, image and GLB model
tools/demo-showcase/     Synthetic seed, immutable updater and GET-only report
```

The facade in `js/api.js` selects existing demo services or `js/live-api.js`. `js/auth-session.js` implements shared provider login/session handling, and `auth/callback.html` loads the same app shell with a root base URL. Keep callback shell markup aligned when changing `index.html`. Telemetry, command execution, durable evidence, permissions and hardware integration remain backend/producer responsibilities.

The authentication boundary in `js/services.js` exposes async `signIn(email)`, `signOut()`, and `getSession()`, plus synchronous `can(permission)` for the UI. A session read returns `{ id, name, email, role }` or `null`. Permission keys are `view`, `operate`, `staff`, `demo`, and `reports`. Client permission checks guide the interface; a real API must independently authorize every request.

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
The original project under `C:\Coding\EE3070\php` is not modified or removed.

## Files needed to run

Run `server.py` together with the complete `css/`, `js/`, `assets/` and `auth/` folders, preserving their relative paths. Local HTTPS hosting requires Python 3.14 or later and uses the standard-library ThreadingHTTPSServer. The production Lambda API remains compatible with Python 3.12. No package installation is required.

The README and `.gitignore` support repository use but are not runtime dependencies. Design references, agent instructions, implementation notes, backend handoff notes and verification screenshots are local development material. Repository tests, deployment scripts and infrastructure templates support the automated checks and deployment.

## GitHub automation

`.github/workflows/ci-deploy.yml` runs on pull requests, `master` pushes and manual dispatch. Node 22 checks JavaScript syntax and runs behavior tests; Python 3.12 runs the production API contract tests, then Python 3.14 runs the full deployment, API, local HTTPS and AI gate tests. The build packages only `index.html`, the exact `auth/callback.html`, `css/`, `js/`, `assets/` and a commit-stamped `release.json`. Preflight runs the production extractor against the actual Git archive, compares every packaged static path and hash, and validates the separate Python API ZIP manifest. Its `deployment-preflight` artifact records the commit, public file inventory and API contract. Run the checks locally with `npm run check`, `npm test`, `python3.14 -m unittest discover -s checks -p '*_test.py'`, `npm run build`, and `python3 scripts/preflight.py`; no dependencies need installing. Preflight normally reviews committed files; `--working-tree` is available for checking local edits before a commit.

Each run starts a disposable SonarQube Community container on the GitHub runner, generates a temporary masked token, scans the website, waits for analysis, and uploads quality-gate conditions, issues and measures as `sonar-report` before enforcing the gate. There is no permanent Sonar dashboard, historical/new-code baseline, PR decoration, or invented coverage. Each PR is scanned as a standalone project because Community does not provide paid branch analysis. A custom overall-code gate requires zero bugs, zero vulnerabilities and zero code smells; the workflow rejects missing, ignored or changed conditions, including an empty default gate. This strict policy evaluates existing code on every run. Original code findings remain visible in the artifact; a failing gate prevents deployment. Artifacts expire after 30 days.

Trusted `master` runs request a blocking AI deployment review through `https://18.163.201.242/ica/harry/v1`. The request includes at most 100 sanitized Sonar rule identifiers, severities, types and line numbers; the passing preflight report's public runtime paths/sizes and API contract; and only these explicit public sources: `.github/workflows/ci-deploy.yml`, `deploy/reshow-deploy.py`, `server.py`, `lambda_handler.py`, `scripts/api-deploy.py`, `infra/studio-api.json` and `infra/frontend.json`. Each source is limited to 12,000 characters with truncation flagged; the preflight report is limited to 128,000 bytes and 1,000 runtime entries. Binary models, other source files, issue prose and environment values are excluded. The API key is decrypted from AWS SSM `/ee3070/reshow/ci/ai-api-key` using GitHub OIDC, masked immediately and sent only in the Authorization header. `AI_MODEL` is an optional repository variable (default `claude-sonnet-5-5`).

The AI gate requires the preflight report to pass and match `GITHUB_SHA`. It requests low reasoning effort with an 8,192-token completion limit and accepts only a nonempty `finish_reason=stop` response with the exact JSON fields `decision`, `blockers` and `review`. A `pass` requires an empty blocker list; a `block` requires concrete evidenced contradictions in the supplied deployment evidence. Explicit blocks, malformed JSON/schema, contradictory decisions, empty/truncated completions, missing keys and API errors fail the job. The `ai-review` artifact preserves the decision or a sanitized failure reason. AI text and commands are never executed. The source pull request workflow receives no AWS or AI credentials. The website does not receive the CI AI key; its separate image-to-JSON feature uses the visitor's Cloudflare credentials. Automated API tests use mocked Cloudflare responses; they do not prove a real generation succeeds.

### Pull request review and merge protection

Push work to a feature branch and open a PR into `master`; do not push changes directly to `master`. `.github/workflows/pr-ai-review.yml` starts after the existing PR CI workflow completes and upserts a bot-owned PR comment with **PASS** or **FAIL**, reasons, the reviewed head SHA and a source-run link. Failed/cancelled CI or a missing required `check`/`sonar` job produces FAIL with available job/step details and links; AI does not run in that case. Passing CI sends the actual complete REST API file patches to the same configured AI gateway/model for a separate review of code correctness, security, integration and data integrity. A structured AI block, missing credentials, API error, invalid/incomplete model response, binary/missing/truncated patch or oversized diff produces FAIL. Reviews accept at most 100 files and 120,000 characters of diff metadata/patches; larger or binary changes need a separate review approach rather than an automatic PASS.

This privileged `workflow_run` uses only the trusted default-branch checkout at `github.sha`, including for fork PRs. It downloads no source-workflow artifacts, executes no PR code, and treats patches as inert model input. It verifies the source workflow path, repository identity, open PR, `master` base and exact head SHA, and ignores superseded heads/runs/rerun attempts. If the source run has no PR associations, it resolves and verifies associated PRs through the commit API. The AWS session is limited to reading the existing AI SSM parameter; the parameter uses the AWS-managed `alias/aws/ssm` key. Model prose is escaped and credentials are redacted before posting. No credentials are passed to the source PR workflow, PR checkout or browser. The result is also a commit status named **`AI PR review`** on the exact reviewed head SHA. The existing deployment AI gate and deployment sequence remain separate.

An owner/admin must enable branch protection or a ruleset for `master`: require a pull request, require the **`check`**, **`sonar`** and **`AI PR review`** status checks, require the branch to be up to date, disallow force pushes/deletion, and disable bypass for administrators and other actors. Comments alone do not enforce merge protection. An account with push access but no admin access cannot configure these rules. The new `workflow_run` only activates after this workflow and script are merged into the default branch; the initial setup PR therefore needs owner review, and the first subsequent PR run registers `AI PR review` for selection as a required status. Re-run the source PR CI after correcting a reported prerequisite failure. If a GitHub API failure prevents posting, the review workflow fails and must be resolved before merging.

After the transfer from `Hello000123/reshow` to `ee3070-team/reshow`, review and separately reinstall `deploy/reshow-deploy.py` as the trusted executable `/usr/local/bin/reshow-deploy` before deploying. Website releases remain under `/opt/ee3070-frontend`. Updating this repository checkout does not update the installed helper used by the constrained SSM documents. Validation, adoption, deployment and rollback must fetch exact commits from the new repository; retain legacy commit `03ce9a0e269337055388d45a04d52cdae411c072` for adoption and rollback.

The last recorded IAM OIDC trust subject is `repo:Hello000123@217039129/reshow@1434123058:ref:refs/heads/master`. The repository ID remains `1434123058`, but the owner has changed to `ee3070-team` (ID `348695846`). Verify the actual organization/repository GitHub OIDC subject settings and emitted `sub` before an approved IAM trust update. If the same repository-ID subject format remains enabled, the expected new subject is `repo:ee3070-team@348695846/reshow@1434123058:ref:refs/heads/master`. Current live OIDC settings, IAM trust, `master` protection and Actions runs remain unverified; these repository edits make no cloud or protection changes. Recheck that protection still requires `check`, `sonar` and `AI PR review` before merging.

The last recorded GitHub AWS role trust rejects new role assumptions at **27 December 2026 at 23:59:59 HKT** (`2026-12-27T15:59:59Z`). A separate recorded role policy explicitly denies AWS actions from that cutoff, including actions using sessions issued earlier. The owner must review and renew access before the cutoff to keep deployments and SSM-backed AI reviews working; workflow code cannot renew these IAM policies.

After checks, Sonar and AI review succeed, deployment assumes `ee3070-reshow-github-actions` in AWS account `835822826851` using OIDC. Before any production change, the constrained SSM document `EE3070-ReShow-ValidateFrontend` validates the installed extractor against the exact commit on existing instance `i-00301bb65c9133a63` in `us-east-1`. For the one existing manual auth release, `EE3070-ReShow-AdoptFrontend` first compares every installed public file path/hash against immutable source commit `03ce9a0e269337055388d45a04d52cdae411c072` before adding the missing release metadata. It does not switch the release or restart the service. An existing valid release marker is a no-op; a different unmarked release, content mismatch or invalid marker blocks deployment. The legacy commit is retained in repository history so rollback can retrieve its exact authenticated runtime. The workflow stages and checks an immutable Lambda API version, deploys that commit's static files, then activates the API alias. CloudFront distribution `E1P2W5YKS25NPQ` is invalidated, then the workflow verifies both `release.json` and `/api/health` report that commit at **https://dkbznu9f5nh9n.cloudfront.net**. Deployment failures after static deployment begins trigger restoration of the previous API alias and static release; rollback errors fail explicitly. Manual dispatch from other branches runs checks only. The AI gateway is separate from website hosting.

Infrastructure prerequisites are the OIDC trust restricted to this repository's immutable-ID `master` subject (the account uses GitHub's repository-ID subject format), least-privilege SSM/parameter/CloudFront/Lambda permissions, the constrained validation, adoption and deployment SSM documents and installed deployment helper, and the reviewed `infra/studio-api.json` and `infra/frontend.json` resources. The private Lambda Function URL requires AWS IAM authentication and CloudFront origin access control; `/api/*` uses HTTPS with POST support and caching disabled. A missing prerequisite causes an explicit failed run. The callback, index and JavaScript use disabled CloudFront caching; the trusted static server marks the callback no-store and removes query strings from request logs. CloudFront overrides Referrer-Policy with no-referrer and does not log access queries. Browser demo records remain local to each origin. The separate staff API enforces real website account permissions; the studio Python API supports image generation. No initial Administrator invitation is sent by deployment.


## PR #7 integration (10 November 2026)

The dashboard, Live showcase, and Website account panel now share the same
`auth-session.js` staff session. `session-auth.js` adapts that session for the
six-tab API facade, preserving request cancellation and exact If-Match versions.
Demo mode still uses the separate local login. In Live mode, the **Website account**
header button opens account administration; **Staff access** manages the directory
and physical cards. The Live showcase and 3D & AI studio tabs from master remain available.
The unused duplicate OAuth implementation has been removed. Facade tests exercise
the shared session, callback mode, identity invalidation and actual request headers.

Both entry shells use one application script and matching complete import maps
at revision `20261110-review-6`. The callback differs only by its root base URL.
Local verification: syntax checks and 67 repository JavaScript tests; earlier
integration checks also covered the local dashboard suite and eight-tab navigation. Synthetic tests cover the
shared callback/session, request versions, cancellation and sign-out; no live
account changes or device operations were performed. Linux deployment and Python
3.14 checks run in GitHub CI, since this workstation has Windows and Python 3.10.


## PR review fixes (10 November 2026)

Removed the hard-coded demo credential and the password field from Demo entry.
Local persona selection still applies role-based demo controls and never grants a
Live session. Provider-backed Live authentication is unchanged. Live request IDs
now require cryptographic UUIDs, email/base64 handling avoids backtracking regexes,
and background notifications explicitly observe subscriber settlement without
holding up accepted writes. Overview refresh still coalesces reads and stops after
its view is disposed. Both entry shells use revision `20261110-review-1`.

The firmware ZIP is a local convenience bundle and is not tracked; review and use
the source under `firmware/ReShow/`.

The repository test suite also opens the real home and callback shells in headless Chromium, verifying that the sign-in UI renders and normalized relative imports share the same versioned API module. CI requires Chromium; local runs without a browser explicitly skip that one integration test. The test uses a temporary profile and a loopback server restricted to public runtime paths.
