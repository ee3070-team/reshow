"""Display-independent operations for the bounded desktop demo editor."""
import hashlib
import base64
import json
import math
import os
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from demo_common import API, Client, NoRedirect, read_private, require_admin, save
import update_demo

EXHIBITS = ("demo-test-clock", "demo-test-vase", "demo-test-camera")
TOKEN_ENDPOINT = "https://reshow-835822826851-us-east-1.auth.us-east-1.amazoncognito.com/oauth2/token"
CLIENT_ID = "uuhbj6m6f94ibe836m2igb70"
SIGN_IN = "Sign in again and choose a fresh private Administrator OAuth file."


def token_expiry(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError()
        claim = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        expiry = claim["exp"]
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
            raise ValueError()
        return expiry  # Renewal hint only; the staff API still authenticates the account.
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise ValueError(SIGN_IN) from None


def administrator_token(filename):
    path = Path(filename)
    try:
        private = read_private(path)
        original = path.read_bytes()
        current = json.loads(original)
    except json.JSONDecodeError:
        raise ValueError(SIGN_IN) from None
    fingerprint = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_mode)
    before = fingerprint(path.stat())
    if path.is_symlink() or not path.is_file() or before[-1] & 0o077 or current != private:
        raise ValueError("The private OAuth file changed. Load current data again.")
    tokens = private.get("tokens") if isinstance(private, dict) else None
    config = private.get("config") if isinstance(private, dict) else None
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
        raise ValueError(SIGN_IN)
    if config is not None and (not isinstance(config, dict) or config.get("token_endpoint") != TOKEN_ENDPOINT
                               or config.get("client_id") != CLIENT_ID):
        raise ValueError(SIGN_IN)
    if token_expiry(tokens["access_token"]) > time.time() + 30:
        return tokens["access_token"]
    if config is None or not isinstance(tokens.get("refresh_token"), str) or not tokens["refresh_token"]:
        raise ValueError(SIGN_IN)
    request = Request(TOKEN_ENDPOINT, data=urlencode({"grant_type": "refresh_token", "client_id": CLIENT_ID,
        "refresh_token": tokens["refresh_token"]}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            renewed = json.loads(response.read(65537))
        if (not isinstance(renewed, dict) or not isinstance(renewed.get("access_token"), str)
                or len(renewed["access_token"]) < 32 or any(c.isspace() for c in renewed["access_token"])
                or token_expiry(renewed["access_token"]) <= time.time() + 30
                or "refresh_token" in renewed and (not isinstance(renewed["refresh_token"], str) or not renewed["refresh_token"])):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Session renewal failed. " + SIGN_IN) from None
    private["tokens"] = {**tokens, **renewed}
    descriptor, temporary = tempfile.mkstemp(prefix=".reshow-renew-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(private, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or fingerprint(path.stat()) != before or path.read_bytes() != original:
            raise ValueError("The private OAuth file changed during renewal. Load current data again.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return renewed["access_token"]


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
        return Client(administrator_token(self.admin_tokens))

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
