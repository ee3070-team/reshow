#!/usr/bin/env python3
"""GET-only verification/reporting. This program never uploads or seeds records."""
import argparse
import csv
import io
import json
from pathlib import Path
from urllib.parse import urlencode

from demo_common import Client, PNG, PNG_SHA256, now, read_private, require_admin, save
from seed_demo import exact, preflight, validate_manifest


def verify(admin, manifest):
    validate_manifest(manifest)
    identity = require_admin(admin)
    records = preflight(admin, manifest)
    if any(value is None for value in records.values()):
        raise ValueError("One or more demo records are missing.")
    case = exact(admin, "showcases", manifest["showcase_id"])["item"]
    if not (case["confirmed_exhibit_id"] == "demo-test-clock" and case["confirmed_profile_version"] == 1
            and case["confirmed_label"]["draft_version"] == 1 and case["lighting_mode"] == "Auto"):
        raise ValueError("Confirmed showcase state differs.")
    commands = [records[row["command_id"]] for row in manifest["commands"]]
    if any(row["status"] in ("pending", "recovery_pending") for row in commands):
        raise ValueError("Demo command remains pending.")
    staff = records["demo-test-card-holder"]
    if not (staff["card_enabled"] and staff["card_device_id"] == manifest["device_id"] and staff["card_id"] == "CARD-DEMO-TEST-01"):
        raise ValueError("Synthetic card registration differs.")
    media = admin.request("/staff-api/media/demo-test-image")[1]
    if media["item"].get("sha256") != PNG_SHA256 or admin.download_png(media["download"]) != PNG:
        raise ValueError("Private sample image does not match.")
    query = {"event_id": "demo-test-event", "interval_minutes": 15, "timezone": "Asia/Hong_Kong"}
    report = admin.request("/staff-api/reports/event?" + urlencode(query))[1]
    if any(report["summary"].get(key) != value for key, value in manifest["expected"].items()):
        raise ValueError("Analytics totals differ.")
    raw_csv = admin.request("/staff-api/reports/event?" + urlencode({**query, "format": "csv"}))[1]
    summary = next(row for row in csv.DictReader(io.StringIO(raw_csv)) if row["section"] == "summary")
    if float(summary["recorded_occupied_seconds"]) != 600:
        raise ValueError("CSV analytics differs.")
    snapshot = exact(admin, "report-snapshots", "demo-test-report")
    if snapshot["report"]["summary"] != report["summary"]:
        raise ValueError("Saved report differs from frozen fixture totals.")
    return {"verified_at": now(), "status": "passed", "identity": identity,
            "data_kind": "synthetic_demo", "hardware_connected": False,
            "showcase_id": manifest["showcase_id"], "device_id": manifest["device_id"],
            "target_record_count": len(records), "command_statuses": {row["command_id"]: row["status"] for row in commands},
            "summary": report["summary"], "data_completeness": report["data_completeness"],
            "freshness": case["freshness"], "no_pending_commands": True,
            "image_bytes_verified": True, "json_csv_totals_match": True, "snapshot_totals_match": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-tokens", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("demo-manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("demo-report.json"))
    args = parser.parse_args()
    admin = Client(read_private(args.admin_tokens)["tokens"]["access_token"])
    result = verify(admin, json.loads(args.manifest.read_text()))
    if any(call["method"] != "GET" for call in admin.calls):
        raise RuntimeError("Report attempted a non-GET operation.")
    save(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
