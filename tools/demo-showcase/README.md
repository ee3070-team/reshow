# EE3070 ReShow demo data and Python updater

Python 3.10+; standard library only for live use. The tools target the existing ReShow API and `demo-test-*` namespace. These are shared exhibition records accessible to the existing `test@example.com` Administrator (`ee3070-staff`), not private per-account records.

The fixture contains one showcase and synthetic device, three exhibit profiles and label drafts, a private 1-pixel PNG for upload testing, one directory-only card holder, exhibit/card scan history, accepted/denied access assertions, six environmental readings, a one-hour analytics event, display/proximity/warning/coverage history, a saved report, and five completed commands. The assigned clock, label, Auto lighting and synthetic card registration are acknowledged by the restricted demo producer; one failed lighting command demonstrates retained Auto state. No command remains pending after successful setup. No real hardware, visitor observation, email, SMS, login account, account role or infrastructure change is made.

The card scan is historical test data. Registration deliberately does not claim current-scan validation. All producer observations and acknowledgements use `source: demo`. Status becomes stale with time; the updater adds fresh observations. Historical readings do not imply complete observation coverage. The report expects **600 occupied seconds, two event starts, 2,100 covered seconds and 1,500 missing seconds**, with one environmental warning. Occupancy events do not count distinct people.

Keep credential files outside this folder and ZIP with permissions `600`. The private producer JSON needs `api_base_url` and `bearer_token`. The private OAuth JSON needs `tokens.access_token`; obtain a fresh production staff sign-in token when expired. Never place the producer token in the browser or share either credential file. These scripts do not change passwords or refresh sign-in sessions.

## Desktop editor

Run `demo_control.py` with a Python build that includes a supported Tkinter/Tk runtime (Tk 8.6 or later recommended). On macOS, use matching current Python and Tk packages; importing the old system Tk 8.5 does not establish that its windows render correctly. The editor code is compatible with Python 3.9+, and its service tests run without Tk or a display.

```sh
python3 demo_control.py --producer-access /private/producer-access.json --admin-tokens /private/admin-tokens.json --plans-dir /path/to/local/demo-plans
```

You can also launch without arguments and choose the private files with **Browse**. The GUI shows file paths, never credential values. Producer access is sufficient for sensor uploads. **Load current data**, profile edits and draft edits require the existing test Administrator's current OAuth file. Replacing an expired OAuth file takes effect on the next action.

1. Select a demo exhibit and click **Load current data** to inspect its profile, draft and latest device observation.
2. In **Sensor reading**, enter temperature, raw brightness, occupied state and optional distance, then click **Send live reading**. The tool saves a frozen plan before its first request, appends the reading/status and verifies their readback. The signed-in website's **Live showcase** tab refreshes automatically while visible.
3. **Retry last upload** reuses the exact saved plan and observation IDs. It uses the saved values even if the input fields were subsequently changed. After restarting the GUI, **Open saved upload** displays the saved sensor values before you retry it. If a network interruption or closing the GUI leaves only one record uploaded, retrying the same plan reconciles that partial upload without creating another reading.
4. The **Exhibit profile** and **Label draft** tabs save using the versions loaded by **Load current data**. A version conflict stops the save; reload and review current data before trying again. Their saved values appear in the website's profile/draft tables, while the device-confirmed exhibit and label require a separate command.

Network operations run outside the GUI thread. Buttons and exhibit selection are disabled during an action. Saved plans contain synthetic payloads and versions; keep them for safe retries. The GUI does not alter the fixed historical analytics fixture or send device commands.

The native **Actions** menu also exposes **Load current data**, **Focus sensor temperature**, **Send live reading** and **Retry last upload**. Keyboard shortcuts are **Ctrl+L** to load and **Ctrl+Enter** to send a sensor reading; macOS also supports **Command+L** and **Command+Enter**. Menu and keyboard actions use the same guarded operations as the buttons.

## Initial setup

The repository keeps the readable generator `demo_fixture.py`; `demo-manifest.json` is a generated local input and is not committed. For an empty demo namespace, `prepare` creates a manifest that freezes initial IDs, timestamps and payloads. The delivered live-demo package includes the original frozen manifest matching the already seeded AWS records: preserve that file and `demo-journal.json` for retries rather than generating another capture for that existing namespace. The desktop editor and updater do not require a seed manifest.

Each command receipt is saved before its POST. Requests use at least one second spacing; writes do not automatically retry. A pending command older than its five-minute deadline stops setup and requires explicit recovery through the existing application protocol.

```sh
python3 seed_demo.py prepare --manifest demo-manifest.json
python3 seed_demo.py preflight --manifest demo-manifest.json --admin-tokens /private/admin-tokens.json --producer-access /private/producer-access.json
python3 seed_demo.py apply --manifest demo-manifest.json --journal demo-journal.json --admin-tokens /private/admin-tokens.json --producer-access /private/producer-access.json --output seed-result.json
```

The API preflight checks all targeted IDs, immutable legacy exhibit cores, tags, directory email/card owners, device owners and pending card reservations before writing. Before an initial live run, independently capture a strongly consistent AWS baseline to check otherwise inaccessible reservation/receipt keys. Repeating a completed unchanged setup performs only GETs and creates no new records. Edited profiles/drafts or conflicting IDs stop setup; they are never reset. This is a sequential seeder, so an interrupted run can leave a partial setup; resume with the same manifest and journal.

## Update the data

Prepare a new immutable reading and status with current UTC time. `--occupied` is optional; omit it for an unoccupied reading. Relative brightness is ADC 0–4095, not calibrated lux. The status copies environmental values and leaves unobserved actuator states null.

```sh
python3 update_demo.py telemetry --plan update-001.json --update-id sample001 --temperature 24.2 --brightness-raw 1800 --occupied --distance-cm 60
python3 update_demo.py apply --plan update-001.json --apply --producer-access /private/producer-access.json --output update-result.json
```

Repeat the same saved plan to retry safely. Prepare another filename and update ID for new data. Both record conflicts are checked before uploading either record. Existing readings and history remain immutable. Two-record upload is sequential; a network interruption may leave one uploaded, and the unchanged plan resumes it safely.

For a versioned profile edit, save an object such as `{"warning_seconds":45}` in `profile-edit.json`. Inspect the current profile version, then:

```sh
python3 update_demo.py profile --plan profile-update.json --exhibit-id demo-test-clock --expected-version 1 --input profile-edit.json
python3 update_demo.py apply --plan profile-update.json --apply --admin-tokens /private/admin-tokens.json
```

Label editing uses `label` in place of `profile` and requires the complete `title`, `collection`, `description` and `image_media_id` object. Use the actual current draft version, or 0 only for an absent draft. A version mismatch stops without overwriting. Management edits **do not apply themselves to the showcase**; the existing assignment/label command workflow is required. After an intentional edit the initial-fixture verifier/seed may report a difference; this protects your edits.

## GET-only verification

```sh
python3 report_demo.py --manifest demo-manifest.json --admin-tokens /private/admin-tokens.json --output demo-report.json
```

This checks every frozen record, linked confirmed state, completed commands, private image bytes, JSON/CSV analytics totals and the saved snapshot. It never uploads or seeds data. It checks the original fixture, so later profile/draft edits or activity inside its historical window can legitimately change the result. API readback is separate from browser rendering and physical-device testing.

## Tests

Portable validation checks use the standard library and make no network requests:

```sh
python3 -m unittest discover -s tools/demo-showcase -p 'test_*.py'
```

The real-handler integration suite is opt-in. It requires Python 3.11+ (for `unittest.TestCase.enterContext`), the owning v0.4.1 backend source tree and its existing test dependencies, including boto3 and its contract validators. Set `RESHOW_TEST_SOURCE` to that backend source root before the same command. Without that variable, integration checks are reported as skipped rather than treated as passed.

## Website

The **Live showcase** tab reads this shared fixture through the signed-in staff session and refreshes automatically while visible. Its refresh button is also available. The original showcase tabs keep their separate browser-local sample data. Private producer credentials belong only in these Python tools.
