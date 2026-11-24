"""Management and producer-acknowledged workflows, separate from immutable telemetry.

The shared demo token proves neither individual staff identity nor physical
execution. A device_id on a receipt is a producer assertion. No timer settles
commands and no command invents completed display history or coverage.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import workflow_store as store
import staff_reads


PROFILE_FIELDS = {"exhibit_id", "name", "description", "tag_id", "collection", "image_media_id", "temperature_min_c", "temperature_max_c", "brightness_min_percent", "brightness_max_percent", "warning_seconds"}
PROFILE_REQUIRED = PROFILE_FIELDS - {"collection", "image_media_id"}
STATUS_FIELDS = {"status_id", "device_id", "recorded_at", "source", "temperature_c", "brightness_percent", "occupied", "distance_cm", "lighting_mode", "lighting_output", "latch", "local_control"}
COMMAND_SECONDS = 300
STALE_SECONDS = 120
MEGA_EPAPER_DEVICE_ID = "reshow-mega-01"
MEGA_EPAPER_TEXT_LIMIT = 180


def fields(api, data, required, optional=()):
    missing, unknown = set(required) - set(data), set(data) - set(required) - set(optional)
    if missing or unknown:
        parts = []
        if missing:
            parts.append("Missing fields: " + ", ".join(sorted(missing)))
        if unknown:
            parts.append("Unknown fields: " + ", ".join(sorted(unknown)))
        raise api.APIError(400, "invalid_payload", "; ".join(parts) + ".")


def enum(api, value, field, choices, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or value not in choices:
        raise api.APIError(400, "invalid_payload", field + " must be one of " + ", ".join(choices) + ".")
    return value


def boolean(api, value, field, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, bool):
        raise api.APIError(400, "invalid_payload", field + " must be a boolean.")
    return value


def utc(api, value, field="recorded_at", future_limit=True):
    if not isinstance(value, str) or not api.UTC_TIMESTAMP.fullmatch(value):
        raise api.APIError(400, "invalid_payload", field + " must be an RFC3339 UTC timestamp ending in Z.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise api.APIError(400, "invalid_payload", field + " is not a valid timestamp.") from None
    if future_limit and parsed > api.now_utc() + timedelta(minutes=5):
        raise api.APIError(400, "invalid_payload", field + " cannot be more than five minutes in the future.")
    return api.timestamp(parsed)


def source(api, value):
    return enum(api, value, "source", ("demo", "device"))


def media_id(api, value):
    if value is None:
        return None
    api.identifier(value, "image_media_id")
    item = store.get(api, "media", value)
    if item["data"].get("status") != "ready":
        raise api.APIError(409, "media_not_ready", "Complete the media upload before using it in a profile or label.")
    return value


def tag_id(api, value, staff_card=False):
    value = api.identifier(value, "card_id" if staff_card else "tag_id").upper()
    if staff_card:
        if not re.fullmatch(r"CARD-[A-Z0-9-]+", value):
            raise api.APIError(400, "invalid_payload", "Staff cards must use a CARD- identifier; exhibit tags cannot grant staff access.")
    elif value.startswith("CARD-"):
        raise api.APIError(400, "invalid_payload", "Exhibit tags cannot use the CARD- staff-card namespace.")
    return value


def version(api, headers, allow_zero=False):
    value = headers.get("if-match", "")
    if not re.fullmatch(r'"?[0-9]{1,9}"?', value) or value.startswith('"') != value.endswith('"'):
        raise api.APIError(428, "precondition_required", "Supply If-Match with the current integer version" + (" (0 for first draft)." if allow_zero else "."))
    number = int(value.strip('"'))
    if number < (0 if allow_zero else 1):
        raise api.APIError(400, "invalid_payload", "If-Match version is outside the allowed range.")
    return number


def require_version(api, previous, expected):
    if int(previous["version"]) != expected:
        raise api.APIError(409, "version_conflict", "If-Match does not match the current version.")


def result(api, item, created=None):
    headers = {"etag": '"' + str(item["version"]) + '"'}
    body = {"item": store.public(item), "consistency": "strong"}
    if created is not None:
        body["status"] = "created" if created else "duplicate"
    return api.response(201 if created else 200, body, headers)


def audit_actions(api, action, status, device_id=None, exhibit_id=None, actor=None, command_id=None, message=None):
    record_id = "activity-" + uuid.uuid4().hex
    data = {"activity_id": record_id, "type": action, "status": status,
            "message": message or (action + " " + status), "identity": "producer assertion; shared demo token"}
    for field, value in (("device_id", device_id), ("exhibit_id", exhibit_id), ("actor", actor), ("command_id", command_id)):
        if value is not None:
            data[field] = value
    item = store.make(api, "activity", record_id, data)
    return [store.put_action(api, item), *store.index_actions(api, item)]


def reservation(api, namespace, value, owner, active=True, command_id=None):
    record_id = namespace + "#" + value
    previous = store.get(api, "unique", record_id, required=False)
    if active and previous and previous["data"].get("active") and previous["data"].get("owner") != owner:
        raise api.APIError(409, "unique_conflict", namespace + " is already registered or reserved for another item.")
    data = {"namespace": namespace, "value": value, "owner": owner, "active": active, "command_id": command_id}
    return store.put_action(api, store.make(api, "unique", record_id, data, previous), previous)


def release_reservation(api, namespace, value, owner):
    previous = store.get(api, "unique", namespace + "#" + value, required=False)
    if previous is None or previous["data"].get("owner") != owner:
        raise api.APIError(409, "registry_conflict", "The unique value reservation does not match its owner.")
    return reservation(api, namespace, value, owner, active=False)


def validate_profile(api, data):
    fields(api, data, PROFILE_REQUIRED, PROFILE_FIELDS - PROFILE_REQUIRED)
    clean = {"exhibit_id": api.identifier(data["exhibit_id"], "exhibit_id"),
             "name": api.text(data["name"], "name", 120), "description": api.text(data["description"], "description", 2000, allow_empty=True),
             "tag_id": tag_id(api, data["tag_id"]), "collection": api.text(data.get("collection", ""), "collection", 120, allow_empty=True),
             "image_media_id": media_id(api, data.get("image_media_id")),
             "warning_seconds": api.number(data["warning_seconds"], "warning_seconds", 1, 3600, integer=True)}
    for field in ("temperature_min_c", "temperature_max_c", "brightness_min_percent", "brightness_max_percent"):
        bounds = (-40, 125) if field.startswith("temperature") else (0, 100)
        clean[field] = api.number(data[field], field, *bounds)
    for lower, upper in (("temperature_min_c", "temperature_max_c"), ("brightness_min_percent", "brightness_max_percent")):
        if clean[lower] >= clean[upper]:
            raise api.APIError(400, "invalid_payload", lower + " must be below " + upper + ".")
    return clean


def legacy_core(data):
    core = {key: data[key] for key in ("exhibit_id", "name", "description", "tag_id", "temperature_min_c", "temperature_max_c")}
    for side in ("min", "max"):
        core["brightness_" + side + "_raw"] = int((Decimal(data["brightness_" + side + "_percent"]) * Decimal(4095) / 100).to_integral_value(rounding=ROUND_HALF_UP))
    return core


def save_profile(api, data, previous=None, expected=None):
    clean = validate_profile(api, data)
    record_id = clean["exhibit_id"]
    old = previous or store.get(api, "exhibit-profile", record_id, required=False)
    if previous is None and old:
        if old.get("create_data", old["data"]) != clean:
            raise api.APIError(409, "id_conflict", "This exhibit_id already belongs to a different create payload.")
        return result(api, old, False)
    if previous:
        require_version(api, previous, expected)
    item = store.make(api, "exhibit-profile", record_id, clean, previous)
    actions = [store.put_action(api, item, previous)]
    if previous is None:
        actions.extend(store.index_actions(api, item))
        # This historical snapshot is never modified by subsequent profile edits.
        core = legacy_core(clean)
        legacy = api.get_table().get_item(Key=api.record_key("exhibit", record_id), ConsistentRead=True).get("Item")
        if legacy:
            # Management tags are case-insensitive; compare a normalized copy
            # while preserving the immutable historical snapshot byte for byte.
            legacy_comparison = {**legacy["data"], "tag_id": tag_id(api, legacy["data"]["tag_id"])}
            if legacy_comparison != core:
                raise api.APIError(409, "legacy_profile_conflict", "Existing immutable exhibit differs from the supplied initial core fields.")
        if legacy is None:
            legacy_item = {**api.record_key("exhibit", record_id), "entity_type": "exhibit", "data": core, "received_at": item["created_at"]}
            actions.append(store.put_action(api, legacy_item))
    if previous is None or previous["data"]["tag_id"] != clean["tag_id"]:
        actions.append(reservation(api, "tag", clean["tag_id"], record_id))
        if previous:
            actions.append(release_reservation(api, "tag", previous["data"]["tag_id"], record_id))
    actions.extend(audit_actions(api, "exhibit_profile", "updated" if previous else "created", exhibit_id=record_id))
    store.transact(api, actions)
    return result(api, item, None if previous else True)


def staff_data(api, data, previous=None):
    fields(api, data, ("staff_id", "name", "email", "role"))
    email = api.text(data["email"], "email", 160).strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise api.APIError(400, "invalid_payload", "email must be a valid address.")
    return {"staff_id": api.identifier(data["staff_id"], "staff_id"), "name": api.text(data["name"], "name", 100),
            "email": email, "role": enum(api, data["role"], "role", ("Administrator", "Operator", "Viewer")),
            "card_id": previous["data"].get("card_id") if previous else None,
            "card_enabled": previous["data"].get("card_enabled", False) if previous else False,
            "card_device_id": previous["data"].get("card_device_id") if previous else None,
            "card_acknowledged_at": previous["data"].get("card_acknowledged_at") if previous else None}


def save_staff(api, data, previous=None, expected=None):
    clean = staff_data(api, data, previous)
    record_id = clean["staff_id"]
    old = previous or store.get(api, "staff", record_id, required=False)
    if previous is None and old:
        if old.get("create_data", old["data"]) != clean:
            raise api.APIError(409, "id_conflict", "This staff_id already belongs to a different create payload.")
        return result(api, old, False)
    if previous:
        require_version(api, previous, expected)
    item = store.make(api, "staff", record_id, clean, previous)
    actions = [store.put_action(api, item, previous)]
    if previous is None:
        actions.extend(store.index_actions(api, item))
    if previous is None or previous["data"]["email"] != clean["email"]:
        actions.append(reservation(api, "email", clean["email"], record_id))
        if previous:
            actions.append(release_reservation(api, "email", previous["data"]["email"], record_id))
    actions.extend(audit_actions(api, "staff_profile", "updated" if previous else "created"))
    store.transact(api, actions)
    return result(api, item, None if previous else True)


def create_showcase(api, data):
    fields(api, data, ("showcase_id", "name", "device_id"))
    clean = {"showcase_id": api.identifier(data["showcase_id"], "showcase_id"), "name": api.text(data["name"], "name", 120),
             "device_id": api.identifier(data["device_id"], "device_id"), "confirmed_exhibit_id": None,
             "assignment_command_id": None, "assignment_acknowledged_at": None,
             "confirmed_label": None, "lighting_mode": None, "lighting_acknowledged_at": None}
    old = store.get(api, "showcase", clean["showcase_id"], required=False)
    if old:
        if old.get("create_data", old["data"]) != clean:
            raise api.APIError(409, "id_conflict", "This showcase_id already belongs to a different create payload.")
        return result(api, old, False)
    item = store.make(api, "showcase", clean["showcase_id"], clean)
    store.transact(api, [store.put_action(api, item), *store.index_actions(api, item),
                         reservation(api, "device", clean["device_id"], clean["showcase_id"]),
                         *audit_actions(api, "showcase", "created", device_id=clean["device_id"])])
    return result(api, item, True)


def bound_showcase(api, device_id):
    registration = store.get(api, "unique", "device#" + device_id, required=False)
    if not registration or not registration["data"].get("active"):
        raise api.APIError(404, "unknown_device", "Register a showcase with this device_id first.")
    return store.get(api, "showcase", registration["data"]["owner"])


def immutable_ingress(api, kind, record_id, clean):
    previous = store.get(api, kind, record_id, required=False)
    if previous:
        if previous["data"] != clean:
            raise api.APIError(409, "id_conflict", "This identifier already belongs to a different payload.")
        return result(api, previous, False)
    bound_showcase(api, clean["device_id"])
    item = store.make(api, kind, record_id, clean)
    actions = [store.put_action(api, item), *store.index_actions(api, item)]
    if kind == "status":
        actions.extend(store.index_actions(api, item, index_kind="status-contact", at=item["created_at"]))
    store.transact(api, actions)
    return result(api, item, True)


def status_data(api, data):
    fields(api, data, STATUS_FIELDS)
    clean = {key: api.identifier(data[key], key) for key in ("status_id", "device_id")}
    clean.update(recorded_at=utc(api, data["recorded_at"]), source=source(api, data["source"]))
    for field, bounds in (("temperature_c", (-40, 125)), ("brightness_percent", (0, 100)), ("distance_cm", (0, 1000))):
        clean[field] = None if data[field] is None else api.number(data[field], field, *bounds)
    clean["occupied"] = boolean(api, data["occupied"], "occupied", nullable=True)
    clean["local_control"] = boolean(api, data["local_control"], "local_control", nullable=True)
    for field, choices in (("lighting_mode", ("Auto", "On", "Off")), ("lighting_output", ("On", "Off")),
                           ("latch", ("Locked", "Unlocked"))):
        clean[field] = enum(api, data[field], field, choices, nullable=True)
    return clean


def showcase_view(api, item):
    device = item["data"]["device_id"]
    latest = store.list_records(api, "status", {"device_id": device, "limit": "1"})["items"]
    contacts = store.list_records(api, "status-contact", {"device_id": device, "limit": "1"})["items"]
    measured = latest[0] if latest else None
    contact = contacts[0] if contacts else None
    def age(value):
        return max(0, int((api.now_utc() - datetime.fromisoformat(value[:-1] + "+00:00")).total_seconds())) if value else None
    measurement_age = age(measured["recorded_at"] if measured else None)
    contact_age = age(contact["created_at"] if contact else None)
    return {**store.public(item), "confirmed_profile_version": item["data"].get("confirmed_profile_version"),
            "label_reconciliation_required": item["data"].get("label_reconciliation_required", False), "device_status": measured, "freshness": {
        "measurement_age_seconds": measurement_age, "contact_age_seconds": contact_age,
        "measurements_stale": measurement_age > STALE_SECONDS if measurement_age is not None else None,
        "contact_stale": contact_age > STALE_SECONDS if contact_age is not None else None, "stale_after_seconds": STALE_SECONDS,
        "meaning": "Status reports are producer assertions. Recent contact does not make an old measurement current."}}


def scan_data(api, data):
    fields(api, data, ("scan_id", "device_id", "recorded_at", "source", "scan_type", "tag_id"), ("scan_attempt_id",))
    clean = {key: api.identifier(data[key], key) for key in ("scan_id", "device_id")}
    clean.update(recorded_at=utc(api, data["recorded_at"]), source=source(api, data["source"]),
                 scan_type=enum(api, data["scan_type"], "scan_type", ("exhibit", "staff_card")))
    clean["tag_id"] = tag_id(api, data["tag_id"], staff_card=clean["scan_type"] == "staff_card")
    if "scan_attempt_id" in data:
        clean["scan_attempt_id"] = api.identifier(data["scan_attempt_id"], "scan_attempt_id")
    return clean


def access_data(api, data):
    fields(api, data, ("attempt_id", "device_id", "recorded_at", "source", "card_id", "decision"), ("reason",))
    clean = {key: api.identifier(data[key], key) for key in ("attempt_id", "device_id")}
    clean.update(recorded_at=utc(api, data["recorded_at"]), source=source(api, data["source"]),
                 card_id=api.identifier(data["card_id"], "card_id").upper(),
                 decision=enum(api, data["decision"], "decision", ("accepted", "denied")),
                 reason=api.text(data.get("reason", ""), "reason", 1000, allow_empty=True))
    return clean


def label_data(api, data):
    fields(api, data, ("title", "collection", "description", "image_media_id"))
    return {"title": api.text(data["title"], "title", 100), "collection": api.text(data["collection"], "collection", 120, allow_empty=True),
            "description": api.text(data["description"], "description", 2000), "image_media_id": media_id(api, data["image_media_id"])}


def save_draft(api, exhibit_id, data, expected):
    store.get(api, "exhibit-profile", exhibit_id)
    clean = {"exhibit_id": exhibit_id, "content": label_data(api, data)}
    old = store.get(api, "label-draft", exhibit_id, required=False)
    if old:
        require_version(api, old, expected)
    elif expected != 0:
        raise api.APIError(409, "version_conflict", "Use If-Match: 0 for the first draft.")
    item = store.make(api, "label-draft", exhibit_id, clean, old)
    actions = [store.put_action(api, item, old)]
    if old is None:
        actions.extend(store.index_actions(api, item))
    actions.extend(audit_actions(api, "label_draft", "saved", exhibit_id=exhibit_id))
    store.transact(api, actions)
    return result(api, item, True if old is None else None)


def command_request(api, data):
    fields(api, data, ("command_id", "showcase_id", "type", "payload"), ("actor",))
    clean = {key: api.identifier(data[key], key) for key in ("command_id", "showcase_id")}
    clean["type"] = enum(api, data["type"], "type", ("assignment", "label", "lighting", "card"))
    clean["actor"] = api.text(data.get("actor", ""), "actor", 120, allow_empty=True)
    payload = data["payload"]
    if not isinstance(payload, dict):
        raise api.APIError(400, "invalid_payload", "payload must be an object.")
    if clean["type"] == "assignment":
        fields(api, payload, ("exhibit_id",))
        payload = {"exhibit_id": api.identifier(payload["exhibit_id"], "exhibit_id")}
    elif clean["type"] == "label":
        fields(api, payload, ("exhibit_id", "draft_version"))
        payload = {"exhibit_id": api.identifier(payload["exhibit_id"], "exhibit_id"),
                   "draft_version": api.number(payload["draft_version"], "draft_version", 1, 999999999, integer=True)}
    elif clean["type"] == "lighting":
        fields(api, payload, ("mode",))
        payload = {"mode": enum(api, payload["mode"], "mode", ("Auto", "On", "Off"))}
    else:
        fields(api, payload, ("staff_id", "card_id", "enabled"), ("scan_id", "scan_attempt_id"))
        scan = {key: api.identifier(payload[key], key) for key in ("scan_id", "scan_attempt_id") if key in payload}
        if scan and len(scan) != 2:
            raise api.APIError(400, "invalid_payload", "Supply scan_id and scan_attempt_id together.")
        payload = {"staff_id": api.identifier(payload["staff_id"], "staff_id"),
                   "card_id": tag_id(api, payload["card_id"], staff_card=True), "enabled": boolean(api, payload["enabled"], "enabled"), **scan}
    clean["payload"] = payload
    return clean


def pending_lock(api, lock_id, command_id, release=False):
    old = store.get(api, "pending", lock_id, required=False)
    if not release and old and old["data"]["status"] in ("pending", "recovery_pending"):
        raise api.APIError(409, "pending_command", "This resource has an unresolved command. A producer receipt or the explicit fence/recovery protocol is required.")
    if release and (old is None or old["data"]["status"] != "pending" or old["data"]["command_id"] != command_id):
        raise api.APIError(409, "stale_acknowledgement", "This command no longer owns the pending resource.")
    item = store.make(api, "pending", lock_id, {"command_id": command_id, "status": "released" if release else "pending"}, old)
    return store.put_action(api, item, old)


def command_deadline(api, command):
    """Legacy pending commands also have a bounded execution window after upgrade."""
    return command["data"].get("execute_before") or api.timestamp(
        datetime.fromisoformat(command["created_at"][:-1] + "+00:00") + timedelta(seconds=COMMAND_SECONDS))


def require_contact(api, device_id):
    contacts = store.list_records(api, "status-contact", {"device_id": device_id, "limit": "1"})["items"]
    if contacts and api.now_utc() - datetime.fromisoformat(contacts[0]["created_at"][:-1] + "+00:00") > timedelta(seconds=STALE_SECONDS):
        raise api.APIError(409, "device_offline", "Known device contact is stale. Upload a fresh device-status observation before requesting a new command.")
    # Unknown contact remains allowed for existing bootstrap/demo clients. It
    # does not imply online status, delivery, execution or acknowledgement.


def validate_scan_reference(api, payload, device_id, command_id):
    if "scan_id" not in payload:
        return []  # Compatible legacy requests do not prove a current scan.
    scan = store.get(api, "scan", payload["scan_id"])
    data = scan["data"]
    if (data["device_id"] != device_id or data["scan_type"] != "staff_card" or data["tag_id"] != payload["card_id"] or
            data.get("scan_attempt_id") != payload["scan_attempt_id"]):
        raise api.APIError(409, "scan_mismatch", "The scan must match this device, staff card and current registration attempt.")
    now = api.now_utc()
    if any(not timedelta(0) <= now - datetime.fromisoformat(value[:-1] + "+00:00") <= timedelta(seconds=STALE_SECONDS)
           for value in (scan["created_at"], data["recorded_at"])):
        raise api.APIError(409, "scan_stale", "The matching scan must have been observed and received within the last 120 seconds.")
    return [reservation(api, "scan", payload["scan_id"], command_id)]


def create_command(api, data):
    request = command_request(api, data)
    record_id = request["command_id"]
    old = store.get(api, "command", record_id, required=False)
    if old:
        if old["data"]["request"] != request:
            raise api.APIError(409, "id_conflict", "This command_id already belongs to a different request.")
        return result(api, old, False)
    case = store.get(api, "showcase", request["showcase_id"])
    require_contact(api, case["data"]["device_id"])
    payload = dict(request["payload"])
    kind = request["type"]
    exhibit = case["data"]["confirmed_exhibit_id"]
    lock_id = "showcase#" + request["showcase_id"] + "#" + kind
    actions = []
    if kind in ("assignment", "label"):
        profile = store.get(api, "exhibit-profile", payload["exhibit_id"])
        exhibit = payload["exhibit_id"]
        if kind == "assignment":
            payload["profile"] = store.public(profile)
        else:
            if case["data"]["confirmed_exhibit_id"] != exhibit:
                raise api.APIError(409, "assignment_required", "Confirm this exhibit's assignment before requesting a label update.")
            draft = store.get(api, "label-draft", exhibit)
            require_version(api, draft, payload["draft_version"])
            content = draft["data"]["content"]
            if case["data"]["device_id"] == MEGA_EPAPER_DEVICE_ID:
                parts = [content["title"], content["description"]]
                if content["collection"]:
                    parts.insert(1, content["collection"])
                rendered = " | ".join(parts)
                if (content["image_media_id"] is not None or len(rendered) > MEGA_EPAPER_TEXT_LIMIT
                        or not re.fullmatch(r"[ -~]+", rendered)):
                    raise api.APIError(400, "unsupported_label_content",
                                       "This e-paper display accepts up to 180 printable ASCII characters "
                                       "including separators, with no line breaks or image. Edit the draft and try again.")
            payload.update(content=content, assignment_command_id=case["data"]["assignment_command_id"])
    elif kind == "card":
        staff = store.get(api, "staff", payload["staff_id"])
        if staff["data"]["card_enabled"] and staff["data"].get("card_device_id") != case["data"]["device_id"]:
            raise api.APIError(409, "card_device_conflict", "Disable the confirmed card on its existing device before moving it to another device.")
        if not payload["enabled"] and (not staff["data"]["card_enabled"] or staff["data"].get("card_id") != payload["card_id"]):
            raise api.APIError(409, "card_conflict", "Disable only the staff member's currently enabled, confirmed card_id.")
        lock_id = "staff#" + payload["staff_id"] + "#card"
        if payload["enabled"]:
            actions.append(reservation(api, "card", payload["card_id"], payload["staff_id"], command_id=record_id))
        actions.extend(validate_scan_reference(api, payload, case["data"]["device_id"], record_id))
    lock_action = pending_lock(api, lock_id, record_id)
    clean = {**request, "request": request, "payload": payload, "device_id": case["data"]["device_id"],
             "exhibit_id": exhibit, "status": "pending", "lock_id": lock_id,
             "protocol": "device-fence-v1", "resource_generation": lock_action["Put"]["Item"]["version"],
             "execute_before": api.timestamp(api.now_utc() + timedelta(seconds=COMMAND_SECONDS)),
             "acknowledgement_id": None, "acknowledged_at": None, "failure_reason": None}
    item = store.make(api, "command", record_id, clean)
    actions.extend([store.put_action(api, item), *store.index_actions(api, item), lock_action,
                    *audit_actions(api, kind, "pending", device_id=clean["device_id"], exhibit_id=exhibit,
                                   actor=request["actor"], command_id=record_id)])
    store.transact(api, actions)
    return result(api, item, True)


def acknowledge(api, command_id, data):
    fields(api, data, ("acknowledgement_id", "device_id", "recorded_at", "source", "success"), ("message",))
    receipt = {"acknowledgement_id": api.identifier(data["acknowledgement_id"], "acknowledgement_id"),
               "device_id": api.identifier(data["device_id"], "device_id"), "recorded_at": utc(api, data["recorded_at"]),
               "source": source(api, data["source"]), "success": boolean(api, data["success"], "success"),
               "message": api.text(data.get("message", ""), "message", 1000, allow_empty=True), "command_id": command_id}
    previous_receipt = store.get(api, "acknowledgement", receipt["acknowledgement_id"], required=False)
    if previous_receipt:
        if previous_receipt.get("create_data", previous_receipt["data"]) != receipt:
            raise api.APIError(409, "id_conflict", "This acknowledgement_id already belongs to a different receipt.")
        command = store.get(api, "command", command_id)
        return api.response(200, {"status": "duplicate", "item": store.public(command), "acknowledgement": store.public(previous_receipt)})
    command = store.get(api, "command", command_id)
    current = command["data"]
    if receipt["device_id"] != current["device_id"]:
        raise api.APIError(409, "device_mismatch", "The receipt device_id does not match the command's bound device.")
    if current["status"] != "pending":
        raise api.APIError(409, "terminal_command", "This command is no longer executable or has a terminal result. Retry an original receipt unchanged or complete its recovery protocol.")
    if max(api.timestamp(api.now_utc()), receipt["recorded_at"]) >= command_deadline(api, command):
        raise api.APIError(409, "command_expired", "The execution window ended; retain the lock and use the explicit fence/recovery protocol to reconcile its outcome.")
    case = store.get(api, "showcase", current["showcase_id"])
    actions, failure = settlement_actions(api, command, case, receipt)
    actions.append(pending_lock(api, current["lock_id"], command_id, release=True))
    terminal = {**current, "status": "failed" if failure else "acknowledged", "acknowledgement_id": receipt["acknowledgement_id"],
                "acknowledged_at": receipt["recorded_at"], "failure_reason": failure, "acknowledgement_source": receipt["source"]}
    command_new = store.make(api, "command", command_id, terminal, command)
    receipt_item = store.make(api, "acknowledgement", receipt["acknowledgement_id"], receipt)
    actions.extend([store.put_action(api, command_new, command), store.put_action(api, receipt_item), *store.index_actions(api, receipt_item),
                    *audit_actions(api, current["type"], terminal["status"], device_id=current["device_id"], exhibit_id=current.get("exhibit_id"),
                                   actor=current["actor"], command_id=command_id, message=failure or "Producer acknowledged " + current["type"] + ".")])
    store.transact(api, actions)
    return api.response(201, {"status": "created", "item": store.public(command_new), "acknowledgement": store.public(receipt_item)})


def settlement_actions(api, command, case, receipt):
    """Apply only a producer-confirmed outcome, never an HTTP timeout or timer."""
    current = command["data"]
    payload, kind = current["payload"], current["type"]
    command_id = current["command_id"]
    failure = None if receipt["success"] else (receipt["message"] or "Producer reported failure; confirmed state was preserved.")
    if receipt["recorded_at"] < command["created_at"]:
        failure = "Receipt predates this request; confirmed state was preserved."
    if kind == "label" and (case["data"]["confirmed_exhibit_id"] != payload["exhibit_id"] or
                            case["data"]["assignment_command_id"] != payload["assignment_command_id"]):
        failure = "The exhibit assignment changed before acknowledgement; confirmed label was preserved."
    actions = []
    staff = store.get(api, "staff", payload["staff_id"]) if kind == "card" else None
    latest_at = (staff["data"].get("card_acknowledged_at") if staff else
                 case["data"].get("assignment_acknowledged_at" if kind == "assignment" else "lighting_acknowledged_at") if kind != "label" else
                 (case["data"].get("confirmed_label") or {}).get("acknowledged_at"))
    if latest_at and receipt["recorded_at"] < latest_at:
        failure = "Receipt is older than confirmed state; confirmed state was preserved."
    if failure is None:
        if kind == "card":
            old_card = staff["data"].get("card_id")
            new_data = {**staff["data"], "card_id": payload["card_id"], "card_enabled": payload["enabled"],
                        "card_device_id": current["device_id"], "card_acknowledged_at": receipt["recorded_at"]}
            actions.append(store.put_action(api, store.make(api, "staff", payload["staff_id"], new_data, staff), staff))
            if payload["enabled"]:
                actions.append(reservation(api, "card", payload["card_id"], payload["staff_id"]))
            else:
                actions.append(release_reservation(api, "card", payload["card_id"], payload["staff_id"]))
            if old_card and old_card != payload["card_id"] and staff["data"]["card_enabled"]:
                actions.append(release_reservation(api, "card", old_card, payload["staff_id"]))
        else:
            new_data = dict(case["data"])
            if kind == "assignment":
                new_data.update(confirmed_exhibit_id=payload["exhibit_id"], assignment_command_id=command_id,
                                assignment_acknowledged_at=receipt["recorded_at"], confirmed_profile_version=payload["profile"]["version"])
            elif kind == "label":
                new_data["confirmed_label"] = {"exhibit_id": payload["exhibit_id"], "content": payload["content"],
                                                "draft_version": payload["draft_version"], "acknowledged_at": receipt["recorded_at"], "command_id": command_id}
                new_data["label_reconciliation_required"] = False
            else:
                new_data.update(lighting_mode=payload["mode"], lighting_acknowledged_at=receipt["recorded_at"])
            actions.append(store.put_action(api, store.make(api, "showcase", current["showcase_id"], new_data, case), case))
    elif kind == "card" and payload["enabled"]:
        # A failed request releases only its pending new card; a previously
        # confirmed active card remains reserved for its current owner.
        same_active = staff["data"]["card_enabled"] and staff["data"].get("card_id") == payload["card_id"]
        actions.append(reservation(api, "card", payload["card_id"], payload["staff_id"], active=bool(same_active)))
    return actions, failure


def request_recovery(api, command_id, data):
    fields(api, data, ("recovery_id", "reason"), ("actor",))
    request = {"recovery_id": api.identifier(data["recovery_id"], "recovery_id"),
               "reason": api.text(data["reason"], "reason", 1000), "actor": api.text(data.get("actor", ""), "actor", 120, allow_empty=True)}
    command = store.get(api, "command", command_id)
    current = command["data"]
    if current.get("recovery"):
        if current["recovery"]["request"] != request:
            raise api.APIError(409, "id_conflict", "This command already has a different recovery request. Retry its original request unchanged.")
        return result(api, command, False)
    if current["status"] != "pending":
        raise api.APIError(409, "terminal_command", "Only unresolved pending commands can enter recovery.")
    lock = store.get(api, "pending", current["lock_id"])
    if lock["data"].get("command_id") != command_id or lock["data"]["status"] != "pending":
        raise api.APIError(409, "stale_acknowledgement", "This command no longer owns the pending resource.")
    lock_new = store.make(api, "pending", current["lock_id"], {"command_id": command_id, "status": "recovery_pending"}, lock)
    recovery = {"request": request, "requested_at": api.timestamp(api.now_utc()), "fence_generation": lock_new["version"],
                "acknowledgement_id": None, "outcome": None, "observed_status_id": None}
    command_new = store.make(api, "command", command_id, {**current, "status": "recovery_pending", "recovery": recovery}, command)
    store.transact(api, [store.put_action(api, command_new, command), store.put_action(api, lock_new, lock),
                         reservation(api, "recovery", request["recovery_id"], command_id),
                         *audit_actions(api, current["type"], "recovery_pending", device_id=current["device_id"],
                                        exhibit_id=current.get("exhibit_id"), actor=request["actor"], command_id=command_id,
                                        message="Recovery requested; resource remains locked until the producer durably fences and reconciles the old request.")])
    return result(api, command_new, True)


def acknowledge_recovery(api, command_id, data):
    fields(api, data, ("acknowledgement_id", "recovery_id", "device_id", "recorded_at", "source", "fenced_generation", "observed_status_id", "outcome"))
    receipt = {key: api.identifier(data[key], key) for key in ("acknowledgement_id", "recovery_id", "device_id", "observed_status_id")}
    receipt.update(command_id=command_id, recorded_at=utc(api, data["recorded_at"]), source=source(api, data["source"]),
                   fenced_generation=api.number(data["fenced_generation"], "fenced_generation", 1, 999999999, integer=True),
                   outcome=enum(api, data["outcome"], "outcome", ("applied", "not_applied", "unknown")))
    previous = store.get(api, "recovery-acknowledgement", receipt["acknowledgement_id"], required=False)
    if previous:
        if previous["data"] != receipt:
            raise api.APIError(409, "id_conflict", "This recovery acknowledgement_id belongs to a different receipt.")
        return api.response(200, {"status": "duplicate", "item": store.public(store.get(api, "command", command_id)),
                                  "recovery_acknowledgement": store.public(previous)})
    command = store.get(api, "command", command_id)
    current = command["data"]
    if receipt["device_id"] != current["device_id"]:
        raise api.APIError(409, "device_mismatch", "The recovery receipt device_id does not match the command.")
    recovery = current.get("recovery")
    if current["status"] != "recovery_pending" or not recovery:
        raise api.APIError(409, "terminal_command", "This command has no pending recovery. Retry the original terminal receipt unchanged.")
    if receipt["recovery_id"] != recovery["request"]["recovery_id"] or receipt["fenced_generation"] != recovery["fence_generation"]:
        raise api.APIError(409, "recovery_mismatch", "The recovery_id and durable fence generation must match this recovery request exactly.")
    if receipt["outcome"] == "unknown":
        raise api.APIError(409, "recovery_outcome_unknown", "Keep this resource locked and fenced until the producer can establish whether the old command was applied.")
    status = store.get(api, "status", receipt["observed_status_id"])
    if (status["data"]["device_id"] != current["device_id"] or status["data"]["recorded_at"] < recovery["requested_at"] or
            status["created_at"] < recovery["requested_at"] or receipt["recorded_at"] < status["data"]["recorded_at"] or
            receipt["recorded_at"] < recovery["requested_at"]):
        raise api.APIError(409, "recovery_observation_mismatch", "Reference a device-status observation made and uploaded after this recovery fence was requested.")
    if not timedelta(0) <= api.now_utc() - datetime.fromisoformat(status["data"]["recorded_at"][:-1] + "+00:00") <= timedelta(seconds=STALE_SECONDS):
        raise api.APIError(409, "recovery_observation_stale", "A recovery needs an observed device status no more than 120 seconds old.")
    lock = store.get(api, "pending", current["lock_id"])
    if lock["data"] != {"command_id": command_id, "status": "recovery_pending"} or int(lock["version"]) != receipt["fenced_generation"]:
        raise api.APIError(409, "stale_acknowledgement", "The recovery no longer owns this resource and generation.")
    state_receipt = {**receipt, "success": receipt["outcome"] == "applied", "message": "Producer recovery confirmed the command was not applied; confirmed state was preserved."}
    case = store.get(api, "showcase", current["showcase_id"])
    actions, failure = settlement_actions(api, command, case, state_receipt)
    stale_label = bool(receipt["outcome"] == "applied" and current["type"] == "label" and failure and failure.startswith("The exhibit assignment changed"))
    if receipt["outcome"] == "applied" and failure and not stale_label:
        raise api.APIError(409, "recovery_state_conflict", failure + " Keep the resource fenced and reconcile its current state before retrying.")
    if stale_label:
        # The producer has fenced the old operation, so a corrective label may
        # now be requested. Preserve the previous confirmed label as history;
        # it is explicitly not a claim about the currently displayed content.
        actions.append(store.put_action(api, store.make(api, "showcase", current["showcase_id"],
                                                        {**case["data"], "label_reconciliation_required": True}, case), case))
    terminal = {**current, "status": "recovered", "recovery": {**recovery, "acknowledgement_id": receipt["acknowledgement_id"],
                "outcome": receipt["outcome"], "observed_status_id": receipt["observed_status_id"], "acknowledged_at": receipt["recorded_at"],
                "source": receipt["source"], "confirmed_state_updated": receipt["outcome"] == "applied" and not stale_label,
                "state_reconciliation_required": stale_label}, "failure_reason": failure if stale_label else current.get("failure_reason")}
    command_new = store.make(api, "command", command_id, terminal, command)
    receipt_item = store.make(api, "recovery-acknowledgement", receipt["acknowledgement_id"], receipt)
    released = store.make(api, "pending", current["lock_id"], {"command_id": command_id, "status": "released"}, lock)
    actions.extend([store.put_action(api, command_new, command), store.put_action(api, released, lock),
                    store.put_action(api, receipt_item), *store.index_actions(api, receipt_item),
                    *audit_actions(api, current["type"], "recovered", device_id=current["device_id"], exhibit_id=current.get("exhibit_id"),
                                   actor=recovery["request"]["actor"], command_id=command_id, message="Producer fenced and reconciled the old request: " + receipt["outcome"] + ".")])
    store.transact(api, actions)
    return api.response(201, {"status": "created", "item": store.public(command_new), "recovery_acknowledgement": store.public(receipt_item)})


def list_query(api, query, allowed=(), require_device=False):
    allowed = {"limit", "cursor", "device_id", "exhibit_id", "start", "end", *allowed}
    if set(query) - allowed:
        raise api.APIError(400, "invalid_query", "Unknown query parameters: " + ", ".join(sorted(set(query) - allowed)) + ".")
    clean = dict(query)
    if require_device:
        api.identifier(clean.get("device_id"), "device_id")
    for field in ("start", "end"):
        if field in clean:
            clean[field] = utc(api, clean[field], field, future_limit=False)
    if "status" in clean:
        enum(api, clean["status"], "status", ("pending", "acknowledged", "failed", "recovery_pending", "recovered"))
    if "type" in clean:
        enum(api, clean["type"], "type", ("assignment", "label", "lighting", "card"))
    if "scan_type" in clean:
        enum(api, clean["scan_type"], "scan_type", ("exhibit", "staff_card"))
    return clean


def no_query(api, query):
    if query:
        raise api.APIError(400, "invalid_query", "This route does not accept query parameters.")


def route(api, method, path, query, event, headers):
    collections = {"exhibit-profiles": ("exhibit-profile", "exhibit_id"), "staff": ("staff", "staff_id"),
                   "showcases": ("showcase", "showcase_id"), "device-status": ("status", "status_id"),
                   "tag-scans": ("scan", "scan_id"), "access-attempts": ("access-attempt", "attempt_id"),
                   "commands": ("command", "command_id"), "workflow-activity": ("activity", "activity_id")}
    tag_prefix = "/api/exhibit-profiles/by-tag/"
    if path.startswith(tag_prefix):
        if method != "GET":
            raise api.APIError(405, "method_not_allowed", "This route accepts GET.")
        no_query(api, query)
        tag = tag_id(api, path[len(tag_prefix):])
        registry = store.get(api, "unique", "tag#" + tag, required=False)
        if not registry or not registry["data"].get("active"):
            raise api.APIError(404, "not_found", "No exhibit profile is registered with this tag.")
        return result(api, store.get(api, "exhibit-profile", registry["data"]["owner"]))
    if path.startswith("/api/label-drafts/"):
        no_query(api, query)
        record_id = api.identifier(path[len("/api/label-drafts/"):], "exhibit_id")
        if method == "GET":
            return result(api, store.get(api, "label-draft", record_id))
        if method == "PUT":
            return save_draft(api, record_id, api.parse_body(event, headers), version(api, headers, allow_zero=True))
        raise api.APIError(405, "method_not_allowed", "This route accepts GET or PUT.")
    for plural, (kind, id_field) in collections.items():
        base = "/api/" + plural
        if path == base:
            if method == "GET":
                extra = {"command": ("status", "type"), "scan": ("scan_type",), "activity": ("type",)}.get(kind, ())
                filtered = list_query(api, query, allowed=extra,
                                      require_device=kind in ("status", "scan", "access-attempt"))
                return api.response(200, staff_reads.list_records(api, kind, filtered))
            if method != "POST" or kind == "activity":
                raise api.APIError(405, "method_not_allowed", "This route accepts GET" + (" or POST." if kind != "activity" else "."))
            no_query(api, query)
            data = api.parse_body(event, headers)
            if kind == "exhibit-profile":
                return save_profile(api, data)
            if kind == "staff":
                return save_staff(api, data)
            if kind == "showcase":
                return create_showcase(api, data)
            if kind == "command":
                return create_command(api, data)
            validators = {"status": status_data, "scan": scan_data, "access-attempt": access_data}
            clean = validators[kind](api, data)
            return immutable_ingress(api, kind, clean[id_field], clean)
        if path.startswith(base + "/"):
            no_query(api, query)
            remainder = path[len(base) + 1:]
            if kind == "command":
                for suffix, handler in (("/recovery-requests", request_recovery), ("/recovery-acknowledgements", acknowledge_recovery)):
                    if remainder.endswith(suffix):
                        record_id = api.identifier(remainder[:-len(suffix)], id_field)
                        if method != "POST":
                            raise api.APIError(405, "method_not_allowed", "This route accepts POST.")
                        return handler(api, record_id, api.parse_body(event, headers))
            if kind == "command" and remainder.endswith("/acknowledgements"):
                record_id = api.identifier(remainder[:-len("/acknowledgements")], id_field)
                if method != "POST":
                    raise api.APIError(405, "method_not_allowed", "This route accepts POST.")
                return acknowledge(api, record_id, api.parse_body(event, headers))
            record_id = api.identifier(remainder, id_field)
            if method == "GET":
                item = store.get(api, kind, record_id)
                if kind == "showcase":
                    return api.response(200, {"item": showcase_view(api, item), "consistency": "strong"}, {"etag": '"' + str(item["version"]) + '"'})
                return result(api, item)
            if method == "PATCH" and kind in ("exhibit-profile", "staff"):
                old = store.get(api, kind, record_id)
                change = api.parse_body(event, headers)
                editable = PROFILE_FIELDS - {"exhibit_id"} if kind == "exhibit-profile" else {"name", "email", "role"}
                if not change or set(change) - editable:
                    raise api.APIError(400, "invalid_payload", "PATCH must contain editable fields only: " + ", ".join(sorted(editable)) + ".")
                data = {key: value for key, value in old["data"].items() if key in (PROFILE_FIELDS if kind == "exhibit-profile" else {"staff_id", "name", "email", "role"})}
                data.update(change)
                saver = save_profile if kind == "exhibit-profile" else save_staff
                return saver(api, data, previous=old, expected=version(api, headers))
            raise api.APIError(405, "method_not_allowed", "This route accepts GET" + (" or PATCH." if kind in ("exhibit-profile", "staff") else "."))
    return None
