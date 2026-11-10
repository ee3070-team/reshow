"""Frozen, explicitly synthetic data for one isolated showcase."""
from datetime import datetime, timedelta, timezone
from demo_common import now, PNG, PNG_SHA256


def build():
    captured = now()
    # Completed, fixed historical hour: safe to rerun without shifting report history.
    left = datetime(2026, 11, 3, 1, tzinfo=timezone.utc)
    def at(minutes):
        return (left + timedelta(minutes=minutes)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    device = "demo-test-device"
    specs = []
    def add(collection, key, payload, role="producer", method="POST"):
        record_id = payload[key]
        specs.append({"collection": collection, "id_key": key, "id": record_id,
                      "role": role, "method": method, "payload": payload})
    for suffix, name, collection in (("clock", "Mechanical Clock", "Timekeeping"),
                                      ("vase", "Ceramic Vase", "Decorative Arts"),
                                      ("camera", "Film Camera", "Photography")):
        exhibit = "demo-test-" + suffix
        add("exhibit-profiles", "exhibit_id", {"exhibit_id": exhibit, "name": "Demo: " + name,
            "description": "Synthetic test exhibit for test@example.com; no physical measurement or visitor study.",
            "collection": collection, "tag_id": "EX-DEMO-TEST-" + suffix.upper(),
            "image_media_id": "demo-test-image", "temperature_min_c": 18, "temperature_max_c": 28,
            "brightness_min_percent": 10, "brightness_max_percent": 80, "warning_seconds": 30}, "admin")
    add("staff", "staff_id", {"staff_id": "demo-test-card-holder", "name": "Demo Card Holder (directory only)",
        "email": "demo-test-card-holder@example.com", "role": "Viewer"}, "admin")
    add("showcases", "showcase_id", {"showcase_id": "demo-test-case", "name": "Demo: EE3070 Complete Showcase",
        "device_id": device}, "admin")
    add("events", "event_id", {"event_id": "demo-test-event", "device_id": device, "source": "demo",
        "start": at(0), "end": at(60), "name": "Demo: one-hour activity and missing-data test"})
    for i, (start, end, exhibit) in enumerate(((0, 30, "clock"), (30, 60, "vase")), 1):
        add("display-history", "display_id", {"display_id": f"demo-test-display-{i}", "device_id": device,
            "source": "demo", "start": at(start), "end": at(end), "exhibit_id": "demo-test-" + exhibit})
    for suffix, start, end in (("carry", -5, 5), ("overlap", 3, 10), ("nested", 5, 7)):
        add("proximity-events", "proximity_event_id", {"proximity_event_id": "demo-test-proximity-" + suffix,
            "device_id": device, "source": "demo", "start": at(start), "end": at(end)})
    add("warnings", "warning_id", {"warning_id": "demo-test-warning", "device_id": device, "source": "demo",
        "start": at(8), "end": at(12), "exhibit_id": "demo-test-clock", "warning_type": "temperature",
        "message": "Synthetic sustained-temperature warning for report testing."})
    for i, (start, end) in enumerate(((0, 30), (45, 50)), 1):
        add("coverage", "coverage_id", {"coverage_id": f"demo-test-coverage-{i}", "device_id": device,
            "source": "demo", "start": at(start), "end": at(end), "streams": ["display", "proximity", "warnings"]})
    for i, (minute, temperature, light, occupied) in enumerate(((0, 23, 1700, False), (5, 24, 1800, True),
            (9, 30, 1900, True), (15, 24, 1500, False), (35, 22, 1200, False), (55, 23, 1600, False)), 1):
        add("readings", "reading_id", {"reading_id": f"demo-test-reading-{i}", "device_id": device,
            "exhibit_id": "demo-test-clock" if minute < 30 else "demo-test-vase", "recorded_at": at(minute),
            "source": "demo", "temperature_c": temperature, "brightness_raw": light,
            "occupied": occupied, "distance_cm": 60 if occupied else None})
    for suffix, kind, tag in (("exhibit", "exhibit", "EX-DEMO-TEST-CLOCK"),
                               ("card", "staff_card", "CARD-DEMO-TEST-01")):
        add("tag-scans", "scan_id", {"scan_id": "demo-test-scan-" + suffix, "device_id": device,
            "source": "demo", "recorded_at": captured, "scan_type": kind, "tag_id": tag,
            **({"scan_attempt_id": "demo-test-card-attempt"} if suffix == "card" else {})})
    for suffix, card, decision in (("accepted", "CARD-DEMO-TEST-01", "accepted"),
                                    ("denied", "CARD-DEMO-TEST-UNKNOWN", "denied")):
        add("access-attempts", "attempt_id", {"attempt_id": "demo-test-access-" + suffix, "device_id": device,
            "source": "demo", "recorded_at": captured, "card_id": card, "decision": decision,
            "reason": "Synthetic demo producer assertion; no physical door or card was used."})
    # Created last so contact remains fresh after the setup and command sequence.
    status = {"status_id": "demo-test-status", "device_id": device, "source": "demo", "recorded_at": captured,
        "temperature_c": 23.5, "brightness_percent": 44, "occupied": False, "distance_cm": None,
        "lighting_mode": "Auto", "lighting_output": "Off", "latch": "Locked", "local_control": True}
    labels = [{"exhibit_id": item["id"], "content": {"title": item["payload"]["name"],
               "collection": item["payload"]["collection"], "description": item["payload"]["description"],
               "image_media_id": "demo-test-image"}} for item in specs if item["collection"] == "exhibit-profiles"]
    commands = [{"command_id": "demo-test-command-" + suffix, "showcase_id": "demo-test-case", "type": kind,
        "payload": payload, "success": success} for suffix, kind, payload, success in (
        ("assignment", "assignment", {"exhibit_id": "demo-test-clock"}, True),
        ("label", "label", {"exhibit_id": "demo-test-clock", "draft_version": 1}, True),
        ("lighting", "lighting", {"mode": "Auto"}, True),
        ("card", "card", {"staff_id": "demo-test-card-holder", "card_id": "CARD-DEMO-TEST-01", "enabled": True}, True),
        ("failed-lighting", "lighting", {"mode": "On"}, False))]
    return {"schema": 1, "data_kind": "synthetic_demo", "hardware_connected": False,
        "staff_id": "ee3070-staff", "email": "test@example.com", "prepared_at": captured,
        "showcase_id": "demo-test-case", "device_id": device,
        "media": {"media_id": "demo-test-image", "content_type": "image/png", "size_bytes": len(PNG), "sha256": PNG_SHA256},
        "records": specs, "labels": labels, "commands": commands, "status": status,
        "snapshot": {"report_id": "demo-test-report", "title": "Demo: activity, environment and missing data",
            "device_id": device, "start": at(0), "end": at(60), "interval_minutes": 15,
            "sections": ["history", "activity", "environment", "missing"]},
        "expected": {"recorded_occupied_seconds": 600, "proximity_event_count": 2,
            "coverage_seconds": 2100, "missing_seconds": 1500, "warning_count": 1}}
