#!/usr/bin/env python3
"""Prepare/apply new immutable observations or versioned demo profile/label edits."""
import argparse
import json
import math
import sys
import uuid
from pathlib import Path

from demo_common import API, Client, now, observation_time, read_private, require_admin, save, scoped, subset


def prepare(args):
    if args.action == "telemetry":
        suffix = args.update_id or uuid.uuid4().hex[:12]
        if not suffix.isalnum() or len(suffix) > 20:
            raise ValueError("--update-id must be 1-20 letters/digits.")
        at, device, exhibit = now(), "demo-test-device", scoped(args.exhibit_id)
        return {"kind": "telemetry", "data_kind": "synthetic_demo", "hardware_connected": False,
            "operations": [{"path": "/api/readings", "method": "POST", "id_key": "reading_id", "payload": {
                "reading_id": "demo-test-update-reading-" + suffix, "device_id": device, "exhibit_id": exhibit,
                "recorded_at": at, "temperature_c": args.temperature, "brightness_raw": args.brightness_raw,
                "occupied": args.occupied, "distance_cm": args.distance_cm, "source": "demo"}},
                {"path": "/api/device-status", "method": "POST", "id_key": "status_id", "payload": {
                "status_id": "demo-test-update-status-" + suffix, "device_id": device, "recorded_at": at, "source": "demo",
                "temperature_c": args.temperature, "brightness_percent": round(args.brightness_raw * 100 / 4095, 6),
                "occupied": args.occupied, "distance_cm": args.distance_cm,
                "lighting_mode": None, "lighting_output": None, "latch": None, "local_control": None}}]}
    value = json.loads(args.input.read_text())
    if not isinstance(value, dict) or not value:
        raise ValueError("--input must contain a nonempty JSON object.")
    allowed = {"name", "description", "collection", "temperature_min_c", "temperature_max_c", "brightness_min_percent", "brightness_max_percent", "warning_seconds"} if args.action == "profile" else {"title", "collection", "description", "image_media_id"}
    if set(value) - allowed or args.action == "label" and set(value) != allowed:
        raise ValueError("Unsupported edit fields; label requires all four label fields.")
    if value.get("image_media_id") is not None:
        scoped(value["image_media_id"])
    return {"kind": args.action, "data_kind": "synthetic_demo", "hardware_connected": False,
            "exhibit_id": scoped(args.exhibit_id), "expected_version": args.expected_version, "payload": value}


def validate_plan(plan):
    if plan.get("data_kind") != "synthetic_demo" or plan.get("hardware_connected") is not False:
        raise ValueError("Only synthetic plans are permitted.")
    if plan.get("kind") == "telemetry":
        if len(plan.get("operations", [])) != 2:
            raise ValueError("Telemetry plan requires a reading and a status observation.")
        for operation, (path, id_key) in zip(plan["operations"], (("/api/readings", "reading_id"), ("/api/device-status", "status_id"))):
            payload = operation["payload"]
            if operation["path"] != path or operation["method"] != "POST" or operation["id_key"] != id_key:
                raise ValueError("Unexpected telemetry operation.")
            scoped(payload[id_key])
            if payload["device_id"] != "demo-test-device" or payload["source"] != "demo":
                raise ValueError("Telemetry may target only the synthetic demo device.")
            if "exhibit_id" in payload:
                scoped(payload["exhibit_id"])
            instant = observation_time(payload["recorded_at"])
            if instant.isoformat(timespec="microseconds").replace("+00:00", "Z") != payload["recorded_at"]:
                raise ValueError("Update timestamps must use six fractional digits as prepared by this tool.")
        reading, status = [operation["payload"] for operation in plan["operations"]]
        reading_fields = {"reading_id", "device_id", "exhibit_id", "recorded_at", "temperature_c", "brightness_raw", "occupied", "distance_cm", "source"}
        status_fields = {"status_id", "device_id", "recorded_at", "source", "temperature_c", "brightness_percent", "occupied", "distance_cm", "lighting_mode", "lighting_output", "latch", "local_control"}
        if set(reading) != reading_fields or set(status) != status_fields:
            raise ValueError("Unexpected observation fields.")
        def number(value, lower, upper, nullable=False, integer=False):
            if nullable and value is None:
                return
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or not lower <= value <= upper or round(value, 6) != value or integer and not isinstance(value, int)):
                raise ValueError("Observation number is outside its permitted range or precision.")
        number(reading["temperature_c"], -40, 125)
        number(reading["brightness_raw"], 0, 4095, integer=True)
        number(reading["distance_cm"], 0, 1000, nullable=True)
        if not isinstance(reading["occupied"], bool):
            raise ValueError("occupied must be a boolean.")
        # JSON booleans and numbers differ even though Python considers True == 1.
        # Validate the second record before uploading either member of the pair.
        number(status["temperature_c"], -40, 125, nullable=True)
        number(status["brightness_percent"], 0, 100, nullable=True)
        number(status["distance_cm"], 0, 1000, nullable=True)
        if not isinstance(status["occupied"], bool):
            raise ValueError("Status occupied must be a boolean.")
        expected_status = {"device_id": reading["device_id"], "recorded_at": reading["recorded_at"], "source": "demo",
            "temperature_c": reading["temperature_c"], "brightness_percent": round(reading["brightness_raw"] * 100 / 4095, 6),
            "occupied": reading["occupied"], "distance_cm": reading["distance_cm"],
            "lighting_mode": None, "lighting_output": None, "latch": None, "local_control": None}
        if not subset(status, expected_status):
            raise ValueError("Status and reading do not describe the same synthetic observation.")
    elif plan.get("kind") in ("profile", "label"):
        scoped(plan["exhibit_id"])
        if not isinstance(plan["expected_version"], int) or isinstance(plan["expected_version"], bool) or plan["expected_version"] < (0 if plan["kind"] == "label" else 1):
            raise ValueError("An explicit current --expected-version is required.")
        allowed = {"name", "description", "collection", "temperature_min_c", "temperature_max_c", "brightness_min_percent", "brightness_max_percent", "warning_seconds"} if plan["kind"] == "profile" else {"title", "collection", "description", "image_media_id"}
        if not isinstance(plan.get("payload"), dict) or not plan["payload"] or set(plan["payload"]) - allowed:
            raise ValueError("Unsupported edit payload.")
        if plan["kind"] == "label":
            if set(plan["payload"]) != allowed:
                raise ValueError("Label edits require all four label fields.")
            if plan["payload"]["image_media_id"] is not None:
                scoped(plan["payload"]["image_media_id"])
    else:
        raise ValueError("Unsupported update kind.")


def apply(plan, admin=None, producer=None):
    validate_plan(plan)
    if plan["kind"] == "telemetry":
        if producer is None:
            raise ValueError("Telemetry needs --producer-access.")
        # All conflicts are detected before the first POST.
        existing = []
        for operation in plan["operations"]:
            payload = operation["payload"]
            old = producer.request(operation["path"] + "/" + payload[operation["id_key"]], expected=(200, 404))[1].get("item")
            if old and not subset(old, payload):
                raise ValueError("An update ID already contains different data; nothing was uploaded.")
            existing.append(old)
        results = []
        for operation, old in zip(plan["operations"], existing):
            if old:
                results.append({"status": "duplicate", "id": operation["payload"][operation["id_key"]]})
                continue
            status, response = producer.request(operation["path"], "POST", operation["payload"], expected=(200, 201))
            readback = producer.request(operation["path"] + "/" + operation["payload"][operation["id_key"]])[1]["item"]
            if not subset(readback, operation["payload"]):
                raise ValueError("Uploaded observation readback differs.")
            results.append({"status": response.get("status"), "http_status": status, "id": operation["payload"][operation["id_key"]]})
        return {"kind": "telemetry", "results": results, "history_overwritten": False}
    if admin is None:
        raise ValueError("Management edits need --admin-tokens.")
    require_admin(admin)
    collection = "exhibit-profiles" if plan["kind"] == "profile" else "label-drafts"
    path = "/staff-api/" + collection + "/" + plan["exhibit_id"]
    old = admin.request(path, expected=(200, 404))[1].get("item")
    current = old["version"] if old else 0
    if current != plan["expected_version"]:
        raise ValueError("Version changed; inspect current data and prepare another plan.")
    method = "PATCH" if plan["kind"] == "profile" else "PUT"
    _, response = admin.request(path, method, plan["payload"], expected=(200, 201), headers={"If-Match": str(current)})
    updated = admin.request(path)[1]["item"]
    content = updated if plan["kind"] == "profile" else updated["content"]
    if not subset(content, plan["payload"]) or updated["version"] != response["item"]["version"]:
        raise ValueError("Versioned update readback differs.")
    return {"kind": plan["kind"], "exhibit_id": plan["exhibit_id"], "version": updated["version"],
            "device_reapplied": False, "note": "Profile/label edits do not change confirmed device state. Apply separately through the supported live staff command API."}


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog="Prepare: update_demo.py telemetry --plan update.json. Apply/retry exact plan: update_demo.py apply --plan update.json --apply --producer-access PRIVATE.json")
    parser.add_argument("action", choices=("telemetry", "profile", "label", "apply"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Explicitly write a previously saved plan")
    parser.add_argument("--producer-access", type=Path)
    parser.add_argument("--admin-tokens", type=Path)
    parser.add_argument("--exhibit-id", default="demo-test-clock")
    parser.add_argument("--update-id")
    parser.add_argument("--temperature", type=float, default=23.5)
    parser.add_argument("--brightness-raw", type=int, default=1800)
    parser.add_argument("--occupied", action="store_true")
    parser.add_argument("--distance-cm", type=float)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.action != "apply":
            if args.apply:
                parser.error("Prepare and apply are separate actions.")
            if args.plan.exists():
                raise ValueError("Plan already exists; use it unchanged or choose another filename.")
            if args.action in ("profile", "label") and (args.input is None or args.expected_version is None):
                parser.error("Profile/label plans require --input and --expected-version.")
            plan = prepare(args)
            validate_plan(plan)
            save(args.plan, plan)
            print("Prepared only; no API calls: " + str(args.plan))
            return
        if not args.apply:
            parser.error("Applying requires --apply.")
        plan = json.loads(args.plan.read_text())
        validate_plan(plan)
        admin = Client(read_private(args.admin_tokens)["tokens"]["access_token"]) if args.admin_tokens else None
        producer = None
        if args.producer_access:
            private = read_private(args.producer_access)
            if private.get("api_base_url") != API:
                raise ValueError("Producer credential targets another API.")
            producer = Client(private["bearer_token"])
        result = apply(plan, admin, producer)
        if args.output:
            save(args.output, result)
        print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print("Update stopped: " + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
