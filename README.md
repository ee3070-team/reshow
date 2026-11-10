# ReShow

A desktop demonstration dashboard for a reusable smart exhibition showcase. Built with HTML, CSS, plain JavaScript ES modules, and a standard-library Python server. The showcase demo needs no build step, framework, package installation or hardware communication. Live website account administration uses the existing AWS Cognito and staff API services. The 3D studio loads the included Three.js r128 and GLTFLoader files. Production serves static files from EC2 through CloudFront and routes the studio API to a separate Python Lambda function over HTTPS.

## Run locally

Use Python 3.14 or later. Open PowerShell in this project directory and run:

```powershell
python server.py
```

Stop any previously running local web server first, then open **http://127.0.0.1:8000** in a desktop browser. If port 8000 is occupied by another application, use `python server.py --port 8001` and open **http://127.0.0.1:8001**. The default mode is plaintext HTTP for local development and does not show a self-signed certificate warning. For optional local HTTPS, provide both `--cert-file` and `--key-file`; use a certificate trusted by the browser and whose subject alternative names include `127.0.0.1` or `localhost`. The server hosts the dashboard and its local showcase API together; do not open `index.html` directly because browsers restrict ES modules on `file:` URLs. Press Ctrl+C in the server terminal to stop it.

Staff sign-in is configured for the production website using the exact `/auth/callback.html` path. The previous HTTP localhost callback is retained in the account configuration, but the local server now requires HTTPS; a trusted HTTPS local callback must be explicitly configured in Cognito and the client allowlist before local staff sign-in can work. PKCE state/verifier expire after ten minutes in sessionStorage; access and refresh tokens remain in memory and are cleared on logout or expiry. Other demo origins can use the local showcase without staff sign-in.

Use the same origin/port to keep saved demo records. Data is stored in that browser's `localStorage`; it is not shared with a server. If storage is unavailable, an on-screen warning explains that changes are held in memory.

## Explore the seven tabs

- **Overview:** confirmed showcase status, environmental readings, assigned exhibit, actual acknowledged label, lighting requests, heatmap, and recent events.
- **Exhibits:** search/filter, create/edit profiles and limits, simulate an exhibit-tag scan, confirm assignment, and inspect illustrative model metadata.
- **Labels:** edit the live monochrome preview, choose a local image, save a draft, and separately request delivery. The previously acknowledged label remains visible until delivery succeeds.
- **Activity:** filter by Hong Kong dates and exhibit, switch heatmap measures, inspect cells with mouse or keyboard, and filter the event log.
- **Reports:** choose sections and filters, generate a stored preview, export its CSV, or use **Print / Save as PDF**. In a regular browser, choose a PDF destination in the print dialog. This is browser printing, not a direct PDF download.
- **Staff access:** sign in through Cognito for live website account administration. An Administrator can manage live invitations, roles and account status. The separate expandable browser-local demo edits sample staff/cards and historical accepted/denied attempts; these sample roles and cards do not grant live website access.
- **3D & AI studio:** explore and adjust the 3D model, import a `.glb` or a trusted JavaScript module exporting a `THREE.Group`, `THREE.Object3D`, or `THREE.Scene`, export four hologram views, and generate exhibit JSON from the included `assets/helmet.jpg` photo or another selected image. Locally this tab uses the same Python server as the rest of the site; the deployed site uses its same-origin HTTPS API.

Three.js module imports use this ES module contract:

```js
export default function createModel(THREE) {
  const group = new THREE.Group();
  // Add meshes and materials to group.
  return group;
}
```

Alternatively, export the object as `createModel`, `createGroup`, `default`, or
`model`. Only import `.js` or `.mjs` modules from trusted sources because the
module executes in the browser. `.ts` files are not directly executed by the
canvas; compile TypeScript to JavaScript first.

Image-to-JSON generation requires Cloudflare Workers AI access. Enter your 32-character Cloudflare Account ID and API token in the **3D & AI studio** tab when generating. Credentials are sent to the Python API for that request only; they are not stored in browser storage or written to disk. Production forwards them through CloudFront HTTPS to Lambda without API caching or application request-body logging. Enable the model and accept any required model terms in Cloudflare before generating. Review the [model license](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/LICENSE) and [acceptable use policy](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/USE_POLICY.md) before using it. The image is processed in memory and is not saved.

There is no Settings tab or remote unlock action. Layouts target desktop widths of 1280, 1440 and 1920 pixels. Exhibit illustrations are local editable SVG assets; model metadata in the Exhibits tab is illustrative.

## RFID two-scan exhibit-change workflow

The RFID hardware is implemented in [hardware/hardware.ino](hardware/hardware.ino).
ESP32 reads the card UID directly. With Wi-Fi enabled, it does not need a computer
or USB serial bridge. The simple data flow is:

```text
RFID card -> ESP32 -> Wi-Fi -> server
                           -> database
```

The ESP32 currently needs four configuration values:

```cpp
const char wifiSsid[] = "YOUR_WIFI_NAME";
const char wifiPassword[] = "YOUR_WIFI_PASSWORD";
const char serverUrl[] = "https://server.example/api/rfid/access-event";
const char rfidStatusUrl[] = "https://server.example/api/rfid/status";
```

`allowedRfidUid` contains the card allowed to open and confirm the change. The
remaining relay pins and reader pins are defined near the top of the Arduino sketch.

For local testing, ESP32 and the computer running `server.py` can be on the same
ordinary Wi-Fi or phone hotspot:

```text
ESP32 -> http://computer-ip:8000 -> server.py -> rfid-events.jsonl
```

For cloud testing, ESP32 should call HTTPS CloudFront/Lambda APIs. `server.py` is
only a local development server; it is not the production database.

GET and POST have different jobs:

| Method | Meaning | ReShow RFID use |
|---|---|---|
| GET | Read current status | ESP32 polls `/api/rfid/status` |
| POST | Submit a new event or command | ESP32 posts scans; staff posts staff-ready confirmation |

A GET request should not itself change an exhibit. A POST request should validate
the card, phase, exhibit and revision before changing server state.

The school network may use enterprise or captive-portal authentication that ESP32
cannot use directly. A phone hotspot or normal router is easier for local testing.
Do not commit Wi-Fi passwords or device secrets to GitHub.

See the simple illustrated walkthrough in
[NO-PUSH-explaination.md](NO-PUSH-explaination/NO-PUSH-explaination.md), and its
flow diagram at [rfid-two-scan.svg](NO-PUSH-explaination/rfid-two-scan.svg).

The ESP32 RFID sketch can protect exhibit changes with two card scans:

```text
First accepted scan
  -> ESP32 opens the door
  -> server state: pending_edit

Staff member manually edits and confirms the exhibit
  -> POST /api/rfid/staff-ready
  -> server state: ready_to_confirm

Second accepted scan
  -> ESP32 checks server state
  -> ESP32 closes the door
  -> server state: idle / confirmed
```

ESP32 events are sent to `POST /api/rfid/access-event`. The ESP32 polls `GET /api/rfid/status` once per second for staff confirmation. The staff-ready request identifies the exhibit and revision:

```json
{
  "exhibit_id": "ex-014",
  "revision": 12
}
```

The allowed RFID UID is defined in [hardware/hardware.ino](hardware/hardware.ino). The first scan is not confirmation: the server remains in `pending_edit` until staff sends `/api/rfid/staff-ready`, and the second scan is required to close the door and finish confirmation. The local workflow is stored in server memory and is reset when the server restarts. It is not yet a staff-authenticated production API, and it must not be exposed without real authorization.

For cloud testing, the ESP32 constants use:

```text
https://dkbznu9f5nh9n.cloudfront.net/api/rfid/access-event
https://dkbznu9f5nh9n.cloudfront.net/api/rfid/status
https://dkbznu9f5nh9n.cloudfront.net/api/rfid/staff-ready
```

These RFID routes are not yet implemented by the deployed Cloud Lambda. `/api/health`
and the Cloudflare AI generator are the currently known active cloud routes.

### Cloud Lambda implementation checklist

The backend team should implement the following before allowing ESP32 to use the cloud routes:

1. Add `GET /api/rfid/status` and return the current workflow state.
2. Add `POST /api/rfid/access-event` and validate the RFID UID, scan phase, card permission and device identity.
3. Add `POST /api/rfid/staff-ready` only for authenticated staff with permission to edit the exhibit.
4. Store `pending_edit`, `ready_to_confirm`, exhibit ID, revision and card UID in a database such as DynamoDB.
5. Use the database for confirmation instead of a Python process-local variable, so Lambda and server restarts do not lose the workflow.
6. Require staff authentication for staff-ready actions; plain RFID UID alone must not change an exhibit or grant website access.
7. Verify the ESP32 as a registered device. Do not keep a shared device secret in public browser JavaScript or GitHub.
8. Add an unsigned or signed short-lived device token before production. Reject repeated, stale and conflicting scan messages.
9. Connect the staff confirmation to the real exhibit update API. Confirmation must validate the current revision so an old edit cannot confirm a different change.
10. Record accepted and denied scans with UTC time for the activity log.
11. Store exhibit revision asset references for the `.glb` model and four view pictures; store the binary files in object storage instead of the database JSON.

Minimum workflow response:

```json
{
  "phase": "ready_to_confirm",
  "uid": "FC6AB516",
  "exhibit_id": "ex-014",
  "revision": 12
}
```

### Exhibit model and view-image storage

The database must store more than RFID workflow state. Each exhibit revision should
also record asset references for one `.glb` 3D model and four view pictures
(front, back, left and right). Use private object storage such as S3 for the binary
files; store stable paths and asset metadata on the exhibit entity:

```json
{
  "exhibit_id": "ex-014",
  "revision": 12,
  "assets": {
    "model_glb": "assets/exhibits/ex-014/model.glb",
    "view_front": "assets/exhibits/ex-014/view-front.jpg",
    "view_back": "assets/exhibits/ex-014/view-back.jpg",
    "view_left": "assets/exhibits/ex-014/view-left.jpg",
    "view_right": "assets/exhibits/ex-014/view-right.jpg"
  }
}
```

Asset database metadata should include media ID, logical path, content type, size
and completion status. Do not place raw `.glb` bytes, base64 data or permanent
storage URLs in exhibit JSON. Serving and replacement should use authenticated,
short-lived download URLs and versioned completed assets. See
[BACKEND_ASSET_UPLOAD.md](BACKEND_ASSET_UPLOAD.md) for the staged upload contract.

Do not enable public access to staff-ready until the route is authenticated. Do not use
`setInsecure()` for a production TLS connection. The CloudFront distribution should route
these RFID APIs to Lambda with POST and GET support, caching disabled and a suitable
request size limit. The current deployed `/api/rfid/*` routes are missing and should not
be used as production access-control endpoints yet.

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
index.html              App shell and seven-tab navigation
auth/callback.html      Matching app shell for the exact Cognito callback
js/auth-session.js      PKCE login and memory-only staff tokens
js/live-accounts.js     Live account controls backed by the staff API
server.py               Local Python server and shared Cloudflare API functions
lambda_handler.py       Production Lambda adapter for the showcase API
BACKEND_ASSET_UPLOAD.md  Backend contract proposal for exhibit .glb and .jpg assets
css/styles.css          Shared desktop components and print rules
css/exhibits.css         Exhibit and label layouts
css/staff.css            Staff directory and card registration layout
js/app.js               Routing, navigation guards, demo dialog
js/ui.js                Icons, safe text escaping, dates, dialogs, heatmaps
js/data.js              Seed records, demo clock and storage version
js/services.js          Async data access, validation and acknowledgements
js/pages/               One UI module per tab
js/pages/technology-showcase.js  3D studio and image-to-JSON generator
css/technology-showcase.css      Showcase-tab components
js/vendor/              Included Three.js r128 and GLTFLoader
assets/                 Local SVG illustrations, image and GLB model
hardware/hardware.ino    ESP32/Mega RFID and local door-relay integration
NO-PUSH-explaination/    Simple team notes, GET/POST explanation and RFID SVG
NO-PUSH-explaination/   Simple local team explanations
```

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
