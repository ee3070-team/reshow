# ReShow demo data and desktop editor

Run commands from this directory. CLI tools need Python 3.10+ and its standard library. The GUI supports Python 3.9+ with Tkinter; use matching current Python/Tk packages on macOS (Tk 8.6+ recommended). Old system Tk 8.5 may import but render blank windows.

## Fixture and credentials

Shared `demo-test-*` records are accessible to the existing `test@example.com` Administrator (`ee3070-staff`). They include one showcase/device, three exhibits/drafts, a private test PNG, a directory-only card holder, scans/access assertions, readings, five terminal commands, analytics and a saved report. All observations/receipts use `source: demo`. No hardware, visitor study, email, SMS, new login or permission change is implied. Historical card scans do not prove current-scan validation.

The fixed event is **3 November 2026, 09:00–10:00 HKT**: **600 occupied seconds, two event starts, 2,100 covered seconds, 1,500 missing seconds and one warning**. Starts are not unique visitors; partial coverage gives recorded lower bounds.

Keep private JSON files outside the package at permissions `600`. Producer JSON needs `api_base_url`/`bearer_token`; Administrator JSON needs `tokens.access_token`. Never place credentials in browser code, plans or shared archives.

CLI actions need a current access token. The GUI renews expired/expiring tokens once using the file's existing `tokens.refresh_token`/`config` and the fixed ReShow Cognito client. It preserves metadata and atomically saves at `600`; concurrent file changes stop the save. Missing/revoked refresh tokens need fresh staff sign-in. The staff API still checks the active Administrator. No password, scope or role changes occur.

## Desktop UI

```sh
python3 demo_control.py --producer-access /private/producer.json --admin-tokens /private/admin.json --plans-dir /private/demo-plans
```

Or choose files with **Browse**. Only paths are displayed. Producer access supports sensor uploads; loading/editing profiles or drafts requires Administrator tokens.

1. Select an exhibit and **Load current data**.
2. Edit temperature, brightness ADC (0–4095), occupied state and optional distance. **Send live reading** saves a frozen plan, appends a reading/status pair and verifies readback. Unobserved actuators remain unknown. The signed-in website's **Live showcase** refreshes while visible.
3. **Retry last upload** uses saved values/IDs even if fields changed. **Open saved upload** restores them after restart. An interruption may leave one record uploaded; retry the unchanged plan to complete it safely.
4. Profile/label saves use the loaded version (`If-Match`). Reload/review after conflicts. Saved tables update separately from device-confirmed content, which requires a separate command. The GUI sends no device commands.

Network work stays off the GUI thread; conflicting actions disable while busy. **Actions** exposes load, temperature focus, send and retry. **Ctrl+L** loads; **Ctrl+Enter** sends. macOS also supports **Command+L/Enter**.

## Seed and verify

`demo_fixture.py` is the readable generator; local `demo-manifest.json` is not committed. Use `prepare` only for an empty namespace. For the already seeded AWS fixture, keep the delivered original frozen manifest and `demo-journal.json`; do not generate another capture. GUI/updater actions need no seed manifest.

```sh
python3 seed_demo.py prepare
python3 seed_demo.py preflight --admin-tokens /private/admin.json --producer-access /private/producer.json
python3 seed_demo.py apply --admin-tokens /private/admin.json --producer-access /private/producer.json
python3 report_demo.py --admin-tokens /private/admin.json --output demo-report.json
```

Preflight checks IDs/bindings/reservations; an initial live run also needs an independent strongly consistent AWS baseline. Setup is sequential: preserve the manifest/journal after interruption. Completed unchanged retries perform only GETs. Conflicting/edited records are never reset. Pending commands expire after five minutes and need explicit recovery. Requests are paced; writes never retry automatically.

`report_demo.py` is GET-only: it verifies frozen records, confirmed states, image bytes, saved snapshot and JSON/CSV totals. Intentional profile/draft edits or later historical-window activity can make strict verification fail. API results do not establish browser/hardware behavior.

## CLI updates and tests

```sh
python3 update_demo.py telemetry --plan update-001.json --temperature 24.2 --brightness-raw 1800 --occupied --distance-cm 60
python3 update_demo.py apply --plan update-001.json --apply --producer-access /private/producer.json
python3 -m unittest discover -s . -p 'test_*.py'
RESHOW_TEST_SOURCE=/path/to/backend python3.14 -m unittest discover -s . -p 'test_*.py'
```

Use new plan names/IDs for new observations and unchanged plans for retries. Both record conflicts are checked before uploading either. `profile`/`label` CLI plans require current versions and JSON input; see `--help`. Updates do not invent historical proximity intervals.

Portable tests need no display, credentials or network. Real-handler integration requires `RESHOW_TEST_SOURCE`, Python 3.11+ and the owning backend's existing boto3/contract dependencies; otherwise it explicitly skips. Original website tabs retain separate browser-local samples. Producer credentials stay in Python.
