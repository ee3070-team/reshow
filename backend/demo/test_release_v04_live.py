#!/usr/bin/env python3
"""Authorized isolated v0.4 HTTPS acceptance, with separate human/demo clients.

Creates synthetic records and explicit producer assertions; never sends email,
sets a password, resets data or claims physical hardware execution. OAuth token
files are private inputs, not evidence. Use a fresh run ID for each full run.
"""
import argparse
import copy
import csv
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from test_workflows_live import Client, PNG, redact


def timestamp(value=None):
    return (value or datetime.now(timezone.utc)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def token_file(path):
    value = json.loads(path.read_text())
    token = value.get("tokens", {}).get("access_token")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("The private OAuth input must contain tokens.access_token.")
    return token


def run(admin, producer, run_id, operator=None, viewer=None, finish_logout=False):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,11}", run_id):
        raise ValueError("--run-id must contain 1-12 identifier characters.")
    prefix = "v04-" + run_id
    ids = {key: prefix + "-" + key for key in ("a", "b", "case", "device", "image", "saved", "card-person")}
    checks = []
    def check(name, condition):
        if not condition:
            raise RuntimeError("Verification failed: " + name)
        checks.append({"check": name, "passed": True})
    def request(client, method, path, data=None, expected=(200,), headers=None):
        return client.request(path, method, data, expected=expected, headers=headers)[1]
    def human(method, path, data=None, expected=(200,), headers=None):
        return request(admin, method, "/staff-api" + path, data, expected, headers)
    def machine(path, data):
        return request(producer, "POST", "/api" + path, data, (200, 201))["item"]
    def create(path, data):
        return human("POST", path, data, (200, 201))["item"]
    def get(path):
        return human("GET", path)["item"]
    def versioned(path, data, version, method="PATCH"):
        return human(method, path, data, (200, 201), {"If-Match": str(version)})["item"]
    def command(identity, kind, payload):
        return create("/commands", {"command_id": prefix + "-" + identity, "showcase_id": ids["case"], "type": kind, "payload": payload})
    def ack(command_item, identity, success=True, device=None):
        latest = request(producer, "GET", "/api/commands/" + command_item["command_id"])["item"]
        check("Producer exact-read fence before synthetic receipt: " + identity,
              latest["status"] == "pending" and latest["protocol"] == "device-fence-v1" and
              latest["resource_generation"] == command_item["resource_generation"] and
              datetime.fromisoformat(latest["execute_before"][:-1] + "+00:00") > datetime.now(timezone.utc))
        data = {"acknowledgement_id": prefix + "-" + identity, "device_id": device or ids["device"],
                "recorded_at": timestamp(), "source": "demo", "success": success, "message": "Synthetic acceptance receipt"}
        return request(producer, "POST", "/api/commands/" + command_item["command_id"] + "/acknowledgements", data, (200, 201))
    def status(identity):
        return machine("/device-status", {"status_id": prefix + "-" + identity, "device_id": ids["device"], "recorded_at": timestamp(),
            "source": "demo", "temperature_c": None, "brightness_percent": None, "occupied": None, "distance_cm": None,
            "lighting_mode": None, "lighting_output": None, "latch": None, "local_control": None, "remote_lighting": True})

    user = human("GET", "/session")["user"]
    check("Real gateway-authorized Administrator session", user["role"] == "Administrator")
    request(producer, "GET", "/staff-api/session", expected=(401,))
    for path in ("/api/exhibit-profiles", "/api/staff", "/api/media/upload-requests", "/api/report-snapshots"):
        request(producer, "POST", path, {}, (403,))
    human("POST", "/coverage", {}, (403,))
    check("Shared demo credential cannot bypass website management; human cannot upload producer evidence", True)

    profiles = {}
    for index, key in enumerate(("a", "b")):
        profiles[key] = create("/exhibit-profiles", {"exhibit_id": ids[key], "name": "Synthetic exhibit " + key.upper(),
            "description": "Isolated backend acceptance fixture", "tag_id": "EX-" + run_id.upper() + "-" + key.upper(),
            "temperature_min_c": 18, "temperature_max_c": 28, "brightness_min_percent": 10,
            "brightness_max_percent": 80, "warning_seconds": 30})
    legacy_before = request(producer, "GET", "/api/exhibits/" + ids["a"])["item"]
    create("/showcases", {"showcase_id": ids["case"], "name": "Synthetic showcase", "device_id": ids["device"]})
    assignment = command("assign", "assignment", {"exhibit_id": ids["a"]})
    check("Pending assignment preserves unknown confirmed state", get("/showcases/" + ids["case"])["confirmed_exhibit_id"] is None)
    wrong = {"acknowledgement_id": prefix + "-wrong-ack", "device_id": "v04-wrong-device", "recorded_at": timestamp(),
             "source": "demo", "success": True, "message": "Synthetic wrong-device receipt"}
    request(producer, "POST", "/api/commands/" + assignment["command_id"] + "/acknowledgements", wrong, (409,))
    first_ack = ack(assignment, "assign-ack")
    stored_ack = first_ack["acknowledgement"]
    same_receipt = {key: stored_ack[key] for key in ("acknowledgement_id", "device_id", "recorded_at", "source", "success", "message")}
    duplicate = request(producer, "POST", "/api/commands/" + assignment["command_id"] + "/acknowledgements", same_receipt)
    check("Bound producer acknowledgement is idempotent and applies captured profile version", duplicate["status"] == "duplicate" and
          get("/showcases/" + ids["case"])["confirmed_profile_version"] == profiles["a"]["version"])
    profiles["a"] = versioned("/exhibit-profiles/" + ids["a"], {"warning_seconds": 45}, profiles["a"]["version"])
    check("Profile edit preserves immutable ingestion record and does not silently apply device thresholds",
          request(producer, "GET", "/api/exhibits/" + ids["a"])["item"] == legacy_before and
          get("/showcases/" + ids["case"])["confirmed_profile_version"] == 1)
    reapplied = command("reapply", "assignment", {"exhibit_id": ids["a"]})
    ack(reapplied, "reapply-ack")
    check("Explicit reassignment applies updated profile version", get("/showcases/" + ids["case"])["confirmed_profile_version"] == profiles["a"]["version"])
    reading = {"reading_id": prefix + "-reading", "device_id": ids["device"], "exhibit_id": ids["a"], "recorded_at": timestamp(),
               "temperature_c": 23, "brightness_raw": 1200, "occupied": False, "distance_cm": None, "source": "demo"}
    machine("/readings", reading)
    check("Management-only exhibit creation supports legacy telemetry without duplicate manual entry",
          request(producer, "GET", "/api/readings/" + reading["reading_id"])["item"]["exhibit_id"] == ids["a"])

    left = datetime(2026, 10, 25, tzinfo=timezone.utc)
    def at(minutes): return timestamp(left + timedelta(minutes=minutes))
    def interval(collection, key, identity, start, end, **extra):
        return machine("/" + collection, {key: prefix + "-" + identity, "device_id": ids["device"], "start": at(start),
                       "end": at(end), "source": "demo", **extra})
    interval("display-history", "display_id", "display-a", 0, 30, exhibit_id=ids["a"])
    interval("display-history", "display_id", "display-b", 30, 60, exhibit_id=ids["b"])
    interval("proximity-events", "proximity_event_id", "crossing", 20, 40)
    interval("warnings", "warning_id", "warning", 10, 12, exhibit_id=ids["a"], warning_type="temperature", message="Synthetic sustained warning")
    query = {"device_id": ids["device"], "start": at(0), "end": at(60), "interval_minutes": 15}
    def report(exhibit, output="json"):
        return human("GET", "/reports/event?" + urlencode({**query, "exhibit_id": ids[exhibit], "format": output}))
    before = report("a")
    check("Exhibit switch splits occupied duration without inventing another event start",
          before["summary"]["recorded_occupied_seconds"] == 600 and before["summary"]["proximity_event_count"] == 1 and
          report("b")["summary"]["recorded_occupied_seconds"] == 600 and report("b")["summary"]["proximity_event_count"] == 0)
    snapshot_request = {"report_id": ids["saved"], "title": "Synthetic v0.4 filtered capture", **query, "exhibit_id": ids["a"], "sections": ["activity"]}
    create("/report-snapshots", snapshot_request)
    saved_before = human("GET", "/report-snapshots/" + ids["saved"])
    interval("proximity-events", "proximity_event_id", "later-upload", 5, 10)
    interval("coverage", "coverage_id", "completion", 0, 60, streams=["display", "proximity", "warnings"])
    current = report("a")
    check("Filtered summaries equal bucket totals and preserve per-exhibit environmental warnings",
          current["summary"]["recorded_occupied_seconds"] == 900 and current["summary"]["proximity_event_count"] == 2 and
          sum(row["recorded_occupied_seconds"] or 0 for row in current["buckets"]) == 900 and current["summary"]["warning_count"] == 1)
    exported = list(csv.DictReader(io.StringIO(report("a", "csv"))))
    summary_csv = next(row for row in exported if row["section"] == "summary")
    check("Filtered JSON and CSV share recorded totals and selector metadata", float(summary_csv["recorded_occupied_seconds"]) == 900 and ids["a"] in summary_csv["detail"])
    profiles["a"] = versioned("/exhibit-profiles/" + ids["a"], {"name": "Later current profile name"}, profiles["a"]["version"])
    human("POST", "/report-snapshots", snapshot_request)
    check("Saved snapshot freezes selector/cutoff/profile/completeness after uploads and edits",
          human("GET", "/report-snapshots/" + ids["saved"]) == saved_before and "data_completeness" in saved_before["report"])
    local_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()
    today = human("GET", "/activity?" + urlencode({"device_id": ids["device"], "start_date": local_today, "end_date": local_today,
                                                  "timezone": "Asia/Hong_Kong", "interval_minutes": 60, "exhibit_id": ids["a"]}))
    check("Hong Kong current-day query captures cutoff and retains future buckets separately",
          today["evaluation"]["status"] == "cutoff_at_capture" and today["evaluation"]["future_seconds"] > 0 and
          all(row["proximity_event_count"] is None for row in today["buckets"] if row["status"] == "future"))

    draft = versioned("/label-drafts/" + ids["a"], {"title": "First draft", "collection": "Synthetic", "description": "Synthetic label", "image_media_id": None}, 0, "PUT")
    label = command("label", "label", {"exhibit_id": ids["a"], "draft_version": draft["version"]})
    versioned("/label-drafts/" + ids["a"], {"title": "Second draft", "collection": "Synthetic", "description": "Unsent revision", "image_media_id": None}, draft["version"], "PUT")
    ack(label, "label-ack")
    check("Sending freezes draft version while later draft edits remain unconfirmed", get("/showcases/" + ids["case"])["confirmed_label"]["draft_version"] == 1)
    status("lighting-ready")
    lighting = command("light-fail", "lighting", {"mode": "On"})
    ack(lighting, "light-fail-ack", False)
    check("Failed producer operation preserves previous confirmed lighting", get("/showcases/" + ids["case"])["lighting_mode"] is None)
    pending = command("light-recover", "lighting", {"mode": "Off"})
    expiry_receipt = {"acknowledgement_id": prefix + "-expiry-ack", "device_id": ids["device"],
        "recorded_at": pending["execute_before"], "source": "demo", "success": True,
        "message": "Synthetic receipt exactly at the command execution deadline"}
    expiry = request(producer, "POST", "/api/commands/" + pending["command_id"] + "/acknowledgements", expiry_receipt, (409,))
    check("Receipt at the bounded execution deadline is rejected without silently releasing pending target", expiry["error"]["code"] == "command_expired" and get("/commands/" + pending["command_id"])["status"] == "pending")
    recovery = create("/commands/" + pending["command_id"] + "/recovery-requests", {"recovery_id": prefix + "-recovery", "reason": "Synthetic ambiguous network response"})
    fresh = status("recovery-status")
    recovery_receipt = {"acknowledgement_id": prefix + "-recovery-ack", "recovery_id": prefix + "-recovery", "device_id": ids["device"],
        "recorded_at": timestamp(), "source": "demo", "fenced_generation": recovery["recovery"]["fence_generation"], "observed_status_id": fresh["status_id"], "outcome": "unknown"}
    request(producer, "POST", "/api/commands/" + pending["command_id"] + "/recovery-acknowledgements", recovery_receipt, (409,))
    recovery_receipt["outcome"] = "not_applied"
    settled = request(producer, "POST", "/api/commands/" + pending["command_id"] + "/recovery-acknowledgements", recovery_receipt, (200, 201))
    check("Durable-fence recovery retains lock for unknown outcome and preserves confirmed state for not-applied", settled["item"]["status"] == "recovered" and get("/showcases/" + ids["case"])["lighting_mode"] is None)
    late = {"acknowledgement_id": prefix + "-late-ack", "device_id": ids["device"], "recorded_at": timestamp(), "source": "demo", "success": True, "message": "Synthetic late receipt"}
    request(producer, "POST", "/api/commands/" + pending["command_id"] + "/acknowledgements", late, (409,))
    replacement = command("light-replacement", "lighting", {"mode": "Auto"})
    ack(replacement, "replacement-ack")
    check("Recovered target is usable with a later generation; old late acknowledgement rejected", get("/showcases/" + ids["case"])["lighting_mode"] == "Auto")

    card_id, attempt_id = "CARD-" + run_id.upper() + "-01", prefix + "-card-attempt"
    create("/staff", {"staff_id": ids["card-person"], "name": "Synthetic card holder", "email": prefix + "-card@example.com", "role": "Viewer"})
    create("/staff", {"staff_id": prefix + "-card-other", "name": "Synthetic second card holder", "email": prefix + "-other@example.com", "role": "Viewer"})
    card_scan = {"scan_id": prefix + "-card-scan", "device_id": ids["device"], "recorded_at": timestamp(), "source": "demo",
                 "scan_type": "staff_card", "tag_id": card_id, "scan_attempt_id": attempt_id}
    machine("/tag-scans", card_scan)
    registration = command("card-register", "card", {"staff_id": ids["card-person"], "card_id": card_id, "enabled": True,
                                                     "scan_id": card_scan["scan_id"], "scan_attempt_id": attempt_id})
    check("Scan-associated card registration remains pending until producer receipt", get("/staff/" + ids["card-person"])["card_enabled"] is False)
    ack(registration, "card-register-ack")
    human("POST", "/commands", {"command_id": prefix + "-card-duplicate", "showcase_id": ids["case"], "type": "card",
          "payload": {"staff_id": prefix + "-card-other", "card_id": card_id, "enabled": True}}, (409,))
    check("Duplicate active cards are rejected without changing original ownership", get("/staff/" + ids["card-person"])["card_enabled"] is True)
    revocation = command("card-revoke", "card", {"staff_id": ids["card-person"], "card_id": card_id, "enabled": False})
    check("Card revocation preserves confirmed access while pending", get("/staff/" + ids["card-person"])["card_enabled"] is True)
    ack(revocation, "card-revoke-ack")
    check("Producer acknowledgement independently revokes physical card access", get("/staff/" + ids["card-person"])["card_enabled"] is False)

    form = human("POST", "/media/upload-requests", {"media_id": ids["image"], "content_type": "image/png", "size_bytes": len(PNG)}, (200, 201))
    admin.upload_png(form["upload"])
    completed = human("POST", "/media/" + ids["image"] + "/complete", {})
    download = human("GET", "/media/" + ids["image"])
    check("Actual private S3 image upload/finalization/readback", completed["item"]["status"] == "ready" and admin.download_png(download["download"]) == PNG)

    for client, role_name in ((operator, "Operator"), (viewer, "Viewer")):
        if client is None:
            continue
        session = request(client, "GET", "/staff-api/session")["user"]
        check("Real " + role_name + " session uses current application role", session["role"] == role_name)
        for method, path in (("GET", "/staff-api/staff"), ("GET", "/staff-api/access-attempts"), ("POST", "/staff-api/accounts/invitations")):
            request(client, method, path, {} if method == "POST" else None, (403,))
        request(client, "POST", "/staff-api/readings", {}, (403,))
        if role_name == "Viewer":
            for path in ("/staff-api/exhibit-profiles", "/staff-api/report-snapshots", "/staff-api/commands", "/staff-api/media/upload-requests"):
                request(client, "POST", path, {}, (403,))
        else:
            request(client, "POST", "/staff-api/commands", {"command_id": prefix + "-forbidden-card", "showcase_id": ids["case"], "type": "card", "payload": {"staff_id": user["staff_id"], "card_id": "CARD-" + run_id.upper(), "enabled": True}}, (403,))
        check(role_name + " direct API permission denials enforced", True)
    if operator:
        person = request(operator, "GET", "/staff-api/session")["user"]
        account_path = "/accounts/" + person["staff_id"]
        account = get(account_path)
        demoted = versioned(account_path, {"role": "Viewer"}, account["version"])
        request(operator, "POST", "/staff-api/commands", {}, (403,))
        restored = versioned(account_path, {"role": "Operator"}, demoted["version"])
        disabled = versioned(account_path, {"status": "disabled"}, restored["version"])
        request(operator, "GET", "/staff-api/session", expected=(403,))
        versioned(account_path, {"status": "active"}, disabled["version"])
        request(operator, "GET", "/staff-api/session", expected=(401,))
        check("Old JWT loses demoted permissions and disabled access; restoration requires new sign-in", True)
    admin_account = get("/accounts/" + user["staff_id"])
    human("PATCH", "/accounts/" + user["staff_id"], {"role": "Viewer"}, (409,), {"If-Match": str(admin_account["version"])})
    check("Last active Administrator cannot be demoted by HTTP", True)
    if viewer:
        request(viewer, "POST", "/staff-api/session/logout", {})
        request(viewer, "GET", "/staff-api/session", expected=(401,))
        check("Application logout immediately rejects an otherwise valid old access token", True)
    if finish_logout:
        human("POST", "/session/logout", {})
        human("GET", "/session", expected=(401,))
        check("Administrator test session signed out", True)
    return {"release": "0.4.0", "run_id": run_id, "source": "demo", "hardware_verified": False,
            "check_count": len(checks), "checks": checks, "ids": ids, "report_totals": current["summary"],
            "requests": {"administrator": admin.responses, "producer": producer.responses,
                         "operator": operator.responses if operator else [], "viewer": viewer.responses if viewer else []}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://urauw4ckw1.execute-api.us-east-1.amazonaws.com")
    parser.add_argument("--producer-state", type=Path, required=True)
    parser.add_argument("--admin-tokens", type=Path, required=True)
    parser.add_argument("--operator-tokens", type=Path)
    parser.add_argument("--viewer-tokens", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--finish-logout", action="store_true")
    args = parser.parse_args()
    producer_token = json.loads(args.producer_state.read_text())["bearer_token"]
    secret_values = [producer_token, token_file(args.admin_tokens)]
    clients = [Client(args.base_url, token) for token in secret_values]
    for optional in (args.operator_tokens, args.viewer_tokens):
        token = token_file(optional) if optional else None
        if token: secret_values.append(token)
        clients.append(Client(args.base_url, token) if token else None)
    result = run(clients[1], clients[0], args.run_id, clients[2], clients[3], args.finish_logout)
    rendered = json.dumps(redact(result), indent=2, allow_nan=False) + "\n"
    if any(secret in rendered for secret in secret_values):
        raise RuntimeError("Evidence secret scan failed; nothing saved.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(json.dumps({"status": "passed", "check_count": result["check_count"], "output": str(args.output)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "message": str(exc)[:250]}), file=sys.stderr)
        sys.exit(1)
