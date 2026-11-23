#!/usr/bin/env python3
"""Prepare or apply a repeatable synthetic showcase. No account or infrastructure changes."""
import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from demo_common import Client, PNG, PNG_SHA256, clients, now, observation_time, require_admin, save, scoped, subset
from demo_fixture import build


def exact(admin, collection, record_id):
    return admin.request(f"/staff-api/{collection}/{scoped(record_id)}", expected=(200, 404))[1]


def item_or_none(value):
    return value.get("item")


def pages(admin, collection):
    query = {"limit": 100}
    for _ in range(100):
        page = admin.request("/staff-api/" + collection + "?" + urlencode(query))[1]
        yield from page["items"]
        cursor = page.get("next_cursor")
        if not cursor:
            return
        query["cursor"] = cursor
    raise ValueError("Unexpectedly large directory; no writes performed.")


def validate_manifest(manifest):
    expected = build()
    if manifest.get("schema") != 1 or manifest.get("staff_id") != "ee3070-staff" or manifest.get("hardware_connected") is not False:
        raise ValueError("Unsupported manifest identity or provenance.")
    # Permit only preparation-time variation. A modified payload needs a separately reviewed tool.
    def without_capture(value):
        value = json.loads(json.dumps(value))
        capture = value.pop("prepared_at")
        observation_time(capture)
        for row in value["records"]:
            if row["collection"] in ("tag-scans", "access-attempts"):
                if row["payload"]["recorded_at"] != capture:
                    raise ValueError("Manifest observation timestamp differs.")
                row["payload"]["recorded_at"] = "CAPTURE"
        if value["status"]["recorded_at"] != capture:
            raise ValueError("Manifest status timestamp differs.")
        value["status"]["recorded_at"] = "CAPTURE"
        return value
    if without_capture(manifest) != without_capture(expected):
        raise ValueError("Manifest payloads differ from the reviewed fixture.")


def preflight(admin, manifest):
    validate_manifest(manifest)
    require_admin(admin)
    found = {}
    # Read every targeted record before issuing any write.
    for spec in manifest["records"]:
        old = item_or_none(exact(admin, spec["collection"], spec["id"]))
        found[spec["id"]] = old
        if old and not subset(old, spec["payload"]):
            raise ValueError("Existing record differs: " + spec["id"])
        if spec["collection"] == "exhibit-profiles":
            payload = spec["payload"]
            legacy = item_or_none(exact(admin, "exhibits", spec["id"]))
            core = {key: payload[key] for key in ("exhibit_id", "name", "description", "tag_id", "temperature_min_c", "temperature_max_c")}
            core.update(brightness_min_raw=410, brightness_max_raw=3276)
            if legacy and not subset(legacy, core):
                raise ValueError("Immutable exhibit core differs: " + spec["id"])
            binding = admin.request("/staff-api/exhibit-profiles/by-tag/" + payload["tag_id"], expected=(200, 404))[1].get("item")
            if binding and binding["exhibit_id"] != spec["id"]:
                raise ValueError("Exhibit tag belongs to another record.")
    for old in pages(admin, "staff"):
        if old.get("email") == "demo-test-card-holder@example.com" and old["staff_id"] != "demo-test-card-holder":
            raise ValueError("Demo email belongs to another directory record.")
        if old.get("card_id") == "CARD-DEMO-TEST-01" and old.get("card_enabled") and old["staff_id"] != "demo-test-card-holder":
            raise ValueError("Demo card belongs to another directory record.")
    for old in pages(admin, "showcases"):
        if old.get("device_id") == manifest["device_id"] and old["showcase_id"] != manifest["showcase_id"]:
            raise ValueError("Demo device belongs to another showcase.")
    for old in pages(admin, "commands"):
        if (old.get("type") == "card" and old.get("status") in ("pending", "recovery_pending")
                and old.get("payload", {}).get("enabled")
                and old["payload"].get("card_id") == "CARD-DEMO-TEST-01"
                and old["command_id"] != "demo-test-command-card"):
            raise ValueError("Demo card is reserved by another pending command.")
    media = item_or_none(exact(admin, "media", manifest["media"]["media_id"]))
    if media and (not subset(media, {k: manifest["media"][k] for k in ("media_id", "content_type", "size_bytes")})
                  or media.get("status") == "ready" and media.get("sha256") != PNG_SHA256):
        raise ValueError("Demo media differs.")
    found[manifest["media"]["media_id"]] = media
    for label in manifest["labels"]:
        old = item_or_none(exact(admin, "label-drafts", label["exhibit_id"]))
        if old and (old.get("content") != label["content"] or old.get("version") != 1):
            raise ValueError("Existing draft differs; seeding never overwrites edits.")
        found["draft:" + label["exhibit_id"]] = old
    for command in manifest["commands"]:
        old = item_or_none(exact(admin, "commands", command["command_id"]))
        payload = {k: v for k, v in command.items() if k != "success"}
        if old:
            if not subset(old.get("request", {}), payload):
                raise ValueError("Existing command request differs.")
            wanted = "acknowledged" if command["success"] else "failed"
            if old["status"] == "pending":
                if datetime.fromisoformat(old["execute_before"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    raise ValueError("Pending demo command expired; explicit recovery is required before resuming.")
            elif old["status"] != wanted or old.get("acknowledgement_source") != "demo" or old.get("acknowledgement_id") != command["command_id"] + "-ack":
                raise ValueError("Existing command outcome differs.")
        found[command["command_id"]] = old
    old = item_or_none(exact(admin, "device-status", manifest["status"]["status_id"]))
    if old and not subset(old, manifest["status"]):
        raise ValueError("Existing final status differs.")
    found[manifest["status"]["status_id"]] = old
    snapshot = exact(admin, "report-snapshots", manifest["snapshot"]["report_id"])
    if snapshot.get("report_id"):
        wanted = manifest["snapshot"]
        report = snapshot.get("report", {})
        if not (snapshot.get("title") == wanted["title"] and set(snapshot.get("sections", [])) == set(wanted["sections"])
                and subset(report, {k: wanted[k] for k in ("device_id", "start", "end")})
                and report.get("interval_minutes") == wanted["interval_minutes"]):
            raise ValueError("Existing report snapshot differs.")
    found[manifest["snapshot"]["report_id"]] = snapshot if "report" in snapshot else None
    return found


def seed(admin, producer, manifest, journal_path):
    existing = preflight(admin, manifest)
    journal_path = Path(journal_path)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {"manifest_sha256": digest, "receipts": {}, "contacts": []}
    if journal.get("manifest_sha256") != digest:
        raise ValueError("Journal belongs to another manifest.")
    created, skipped = [], []
    def create(spec):
        if existing.get(spec["id"]):
            skipped.append(spec["id"])
            return
        client = admin if spec["role"] == "admin" else producer
        path = ("/staff-api/" if spec["role"] == "admin" else "/api/") + spec["collection"]
        client.request(path, spec.get("method", "POST"), spec["payload"], expected=(200, 201))
        created.append(spec["id"])
    media = manifest["media"]
    if not existing.get(media["media_id"]) or existing[media["media_id"]]["status"] != "ready":
        form = admin.request("/staff-api/media/upload-requests", "POST", {k: media[k] for k in ("media_id", "content_type", "size_bytes")}, expected=(200, 201))[1]
        if form["item"]["status"] != "ready":
            admin.upload_png(form["upload"])
            admin.request("/staff-api/media/" + media["media_id"] + "/complete", "POST", {})
        created.append(media["media_id"])
    else:
        skipped.append(media["media_id"])
    for spec in manifest["records"]:
        if spec["collection"] != "access-attempts":
            create(spec)
    for label in manifest["labels"]:
        if existing.get("draft:" + label["exhibit_id"]):
            skipped.append("draft:" + label["exhibit_id"])
            continue
        admin.request("/staff-api/label-drafts/" + label["exhibit_id"], "PUT", label["content"], expected=(200, 201), headers={"If-Match": "0"})
        created.append("draft:" + label["exhibit_id"])
    for command in manifest["commands"]:
        command_id = command["command_id"]
        old = existing.get(command_id)
        if old and old["status"] != "pending":
            skipped.append(command_id)
            continue
        if not old:
            contact = {**manifest["status"], "status_id": "demo-test-contact-" + uuid.uuid4().hex[:12], "recorded_at": now()}
            journal["contacts"].append(contact)
            save(journal_path, journal)
            producer.request("/api/device-status", "POST", contact, expected=(200, 201))
            old = admin.request("/staff-api/commands", "POST", {k: v for k, v in command.items() if k != "success"}, expected=(200, 201))[1]["item"]
        latest = producer.request("/api/commands/" + command_id)[1]["item"]
        if latest["status"] != "pending" or latest["device_id"] != manifest["device_id"] or latest["resource_generation"] != old["resource_generation"]:
            raise ValueError("Command fence changed; inspect before continuing.")
        if command_id not in journal["receipts"]:
            journal["receipts"][command_id] = {"acknowledgement_id": command_id + "-ack", "device_id": manifest["device_id"],
                "recorded_at": now(), "source": "demo", "success": command["success"],
                "message": "Synthetic demo producer receipt; no physical operation."}
            save(journal_path, journal)
        producer.request("/api/commands/" + command_id + "/acknowledgements", "POST", journal["receipts"][command_id], expected=(200, 201))
        created.append(command_id)
    for spec in manifest["records"]:
        if spec["collection"] == "access-attempts":
            create(spec)
    create({"collection": "device-status", "id": manifest["status"]["status_id"], "role": "producer", "payload": manifest["status"]})
    if not existing.get(manifest["snapshot"]["report_id"]):
        admin.request("/staff-api/report-snapshots", "POST", manifest["snapshot"], expected=(200, 201))
        created.append(manifest["snapshot"]["report_id"])
    else:
        skipped.append(manifest["snapshot"]["report_id"])
    return {"data_kind": "synthetic_demo", "hardware_connected": False, "created_or_completed": created,
            "skipped_existing": skipped, "created_or_completed_count": len(created), "skipped_count": len(skipped)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "preflight", "apply"))
    parser.add_argument("--manifest", type=Path, default=Path("demo-manifest.json"))
    parser.add_argument("--journal", type=Path, default=Path("demo-journal.json"))
    parser.add_argument("--admin-tokens", type=Path)
    parser.add_argument("--producer-access", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            if args.manifest.exists():
                validate_manifest(json.loads(args.manifest.read_text()))
            else:
                save(args.manifest, build())
            print("Frozen synthetic manifest ready: " + str(args.manifest))
            return
        if not args.admin_tokens or not args.producer_access:
            parser.error("--admin-tokens and --producer-access are required for API actions")
        admin, producer = clients(args.admin_tokens, args.producer_access)
        manifest = json.loads(args.manifest.read_text())
        result = {"status": "preflight_passed", "existing_records": sum(v is not None for v in preflight(admin, manifest).values())} if args.action == "preflight" else seed(admin, producer, manifest, args.journal)
        if args.output:
            save(args.output, result)
        print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print("Demo stopped: " + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
