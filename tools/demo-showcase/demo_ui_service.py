"""Display-independent operations for the bounded desktop demo editor."""
import hashlib
import json
import math
import uuid
from pathlib import Path
from types import SimpleNamespace

from demo_common import API, Client, read_private, require_admin, save
import update_demo

EXHIBITS = ("demo-test-clock", "demo-test-vase", "demo-test-camera")


def exhibit_id(value):
    if value not in EXHIBITS:
        raise ValueError("Choose one of the three demo exhibits.")
    return value


def number(value, label, lower, upper, integer=False, optional=False):
    if optional and (value is None or str(value).strip() == ""):
        return None
    if isinstance(value, bool):
        raise ValueError(label + " must be a number.")
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        raise ValueError(label + " must be a number.") from None
    if (not math.isfinite(parsed) or not lower <= parsed <= upper
            or round(parsed, 6) != parsed or integer and not parsed.is_integer()):
        raise ValueError(label + " must be " + ("an integer " if integer else "") +
                         "between " + str(lower) + " and " + str(upper) + ".")
    return int(parsed) if integer else parsed


def text(value, label, maximum, allow_empty=False):
    if not isinstance(value, str) or not allow_empty and not value.strip() or len(value) > maximum:
        raise ValueError(label + " must contain " + ("0" if allow_empty else "1") +
                         "–" + str(maximum) + " characters.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError(label + " contains unsupported control characters.")
    return value


class DemoEditor:
    def __init__(self, plans_dir, producer_access=None, admin_tokens=None, producer=None, admin=None):
        self.plans_dir = Path(plans_dir)
        self.producer_access, self.admin_tokens = producer_access, admin_tokens
        self._producer, self._admin = producer, admin
        self.last_plan = None
        self.last_digest = None

    def producer(self):
        if self._producer is not None:
            return self._producer
        if not self.producer_access:
            raise ValueError("Choose the private producer access file first.")
        private = read_private(self.producer_access)
        if not isinstance(private, dict) or private.get("api_base_url") != API:
            raise ValueError("The producer file targets another API.")
        if not isinstance(private.get("bearer_token"), str):
            raise ValueError("The producer file does not contain a private API credential.")
        return Client(private["bearer_token"])

    def admin(self):
        if self._admin is not None:
            return self._admin
        if not self.admin_tokens:
            raise ValueError("Choose a current private Administrator token file first.")
        # Re-read each action so replacing an expired token file takes effect.
        private = read_private(self.admin_tokens)
        tokens = private.get("tokens") if isinstance(private, dict) else None
        if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
            raise ValueError("The Administrator file does not contain an access token.")
        return Client(tokens["access_token"])

    def load(self, selected):
        selected = exhibit_id(selected)
        admin = self.admin()
        require_admin(admin)
        showcase = admin.request("/staff-api/showcases/demo-test-case")[1]["item"]
        if showcase.get("device_id") != "demo-test-device":
            raise ValueError("The demo showcase has an unexpected device binding.")
        profile = admin.request("/staff-api/exhibit-profiles/" + selected)[1]["item"]
        draft = admin.request("/staff-api/label-drafts/" + selected, expected=(200, 404))[1].get("item")
        if profile.get("exhibit_id") != selected or draft and draft.get("exhibit_id") != selected:
            raise ValueError("The returned exhibit does not match the selection.")
        if (not isinstance(profile.get("version"), int) or isinstance(profile["version"], bool)
                or profile["version"] < 1 or "name" not in profile):
            raise ValueError("The profile response has no current version.")
        if draft and (not isinstance(draft.get("version"), int) or isinstance(draft["version"], bool)
                      or draft["version"] < 1 or not isinstance(draft.get("content"), dict)):
            raise ValueError("The label response has no current draft version/content.")
        if showcase.get("device_status") is not None and not isinstance(showcase["device_status"], dict):
            raise ValueError("The showcase returned an invalid device observation.")
        return {"showcase": showcase, "profile": profile, "draft": draft}

    def _save_plan(self, plan):
        update_demo.validate_plan(plan)
        path = self.plans_dir / ("update-" + uuid.uuid4().hex + ".json")
        save(path, plan)
        return path

    def send_telemetry(self, selected, temperature, brightness, occupied, distance):
        if not isinstance(occupied, bool):
            raise ValueError("Occupied must be a checkbox value.")
        args = SimpleNamespace(action="telemetry", update_id=None, exhibit_id=exhibit_id(selected),
            temperature=number(temperature, "Temperature", -40, 125),
            brightness_raw=number(brightness, "Brightness ADC", 0, 4095, integer=True),
            occupied=occupied, distance_cm=number(distance, "Distance", 0, 1000, optional=True))
        plan = update_demo.prepare(args)
        self.last_plan = self._save_plan(plan)
        self.last_digest = hashlib.sha256(self.last_plan.read_bytes()).hexdigest()
        result = update_demo.apply(plan, producer=self.producer())
        return {"result": result, "plan": str(self.last_plan)}

    def select_retry(self, path):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Choose a regular saved upload plan.")
        raw = path.read_bytes()
        plan = json.loads(raw)
        update_demo.validate_plan(plan)
        if plan["kind"] != "telemetry":
            raise ValueError("Only immutable reading/status uploads can be retried here.")
        self.last_plan, self.last_digest = path, hashlib.sha256(raw).hexdigest()
        return {"plan": str(path), "reading": plan["operations"][0]["payload"]}

    def retry_telemetry(self):
        if self.last_plan is None:
            raise ValueError("Send an observation or open a saved upload plan first.")
        raw = self.last_plan.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.last_digest:
            raise ValueError("The saved plan changed. Restore the original plan before retrying.")
        plan = json.loads(raw)
        update_demo.validate_plan(plan)
        if plan["kind"] != "telemetry":
            raise ValueError("Only immutable reading/status uploads can be retried here.")
        return {"result": update_demo.apply(plan, producer=self.producer()), "plan": str(self.last_plan)}

    def save_edit(self, kind, selected, version, values):
        selected = exhibit_id(selected)
        if kind not in ("profile", "label"):
            raise ValueError("Choose a profile or label draft edit.")
        if isinstance(version, bool) or not isinstance(version, int) or version < (1 if kind == "profile" else 0):
            raise ValueError("Load the current version before saving.")
        if kind == "profile":
            payload = {"name": text(values.get("name"), "Name", 120),
                "description": text(values.get("description"), "Description", 2000, True),
                "collection": text(values.get("collection"), "Collection", 120, True),
                "temperature_min_c": number(values.get("temperature_min_c"), "Minimum temperature", -40, 125),
                "temperature_max_c": number(values.get("temperature_max_c"), "Maximum temperature", -40, 125),
                "brightness_min_percent": number(values.get("brightness_min_percent"), "Minimum brightness", 0, 100),
                "brightness_max_percent": number(values.get("brightness_max_percent"), "Maximum brightness", 0, 100),
                "warning_seconds": number(values.get("warning_seconds"), "Warning seconds", 1, 3600, integer=True)}
            if payload["temperature_min_c"] >= payload["temperature_max_c"] or payload["brightness_min_percent"] >= payload["brightness_max_percent"]:
                raise ValueError("Each minimum must be below its maximum.")
        else:
            payload = {"title": text(values.get("title"), "Title", 100),
                "collection": text(values.get("collection"), "Collection", 120, True),
                "description": text(values.get("description"), "Description", 2000),
                "image_media_id": values.get("image_media_id") or None}
        plan = {"kind": kind, "data_kind": "synthetic_demo", "hardware_connected": False,
                "exhibit_id": selected, "expected_version": version, "payload": payload}
        path = self._save_plan(plan)
        result = update_demo.apply(plan, admin=self.admin())
        return {"result": result, "plan": str(path)}
