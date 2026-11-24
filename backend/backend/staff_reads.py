"""Least-privilege workflow views and confidential staff pagination.

Restricted lists evaluate one bounded index page, then project authorized rows.
Their continuation keys are authenticated and encrypted; an empty page never
discloses the internal identity of the restricted row at its boundary.
"""
import base64
import json
import os

import auth
import workflow_store as store


EXHIBITION_TYPES = ("assignment", "label", "lighting")
COMMAND_STATUSES = ("pending", "acknowledged", "failed", "recovery_pending", "recovered")
META_FIELDS = ("version", "created_at", "updated_at")
ACTIVITY_FIELDS = ("activity_id", "type", "status", "device_id", "exhibit_id", "command_id", *META_FIELDS)
SCAN_FIELDS = ("scan_id", "device_id", "recorded_at", "source", "scan_type", "tag_id", "scan_attempt_id", *META_FIELDS)
COMMAND_FIELDS = ("command_id", "showcase_id", "device_id", "type", "status", "exhibit_id", "protocol",
                  "resource_generation", "execute_before", "acknowledgement_id", "acknowledged_at",
                  "acknowledgement_source", *META_FIELDS)
RECOVERY_FIELDS = ("requested_at", "fence_generation", "acknowledgement_id", "outcome", "observed_status_id",
                   "acknowledged_at", "source", "confirmed_state_updated", "state_reconciliation_required")
PROFILE_FIELDS = ("exhibit_id", "name", "description", "tag_id", "collection", "image_media_id",
                  "temperature_min_c", "temperature_max_c", "brightness_min_percent", "brightness_max_percent",
                  "warning_seconds", *META_FIELDS)
LABEL_FIELDS = ("title", "collection", "description", "image_media_id")
CURSOR_KEY_ID = "staff-cursor-key-v1"
CURSOR_SECONDS = 900


def pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def restricted(kind):
    person = auth.principal.get()
    return bool(person and person["kind"] == "human" and person["role"] != "Administrator"
                and kind in ("scan", "command", "activity"))


def project(api, kind, value):
    """Return only the explicit public view, or None for a forbidden record."""
    if not restricted(kind):
        return value
    if kind == "scan":
        if auth.principal.get()["role"] != "Operator" or value.get("scan_type") != "exhibit":
            return None
        return pick(value, SCAN_FIELDS)
    if value.get("type") not in EXHIBITION_TYPES or value.get("status") not in COMMAND_STATUSES:
        return None
    if kind == "activity":
        if value.get("command_id"):
            command = store.get(api, "command", value["command_id"], required=False)
            if command is None or command["data"].get("type") != value["type"]:
                return None
        return pick(value, ACTIVITY_FIELDS)
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return None
    visible = pick(value, COMMAND_FIELDS)
    if value["type"] == "lighting":
        visible["payload"] = pick(payload, ("mode",))
    elif value["type"] == "assignment":
        visible["payload"] = pick(payload, ("exhibit_id",))
        if isinstance(payload.get("profile"), dict):
            visible["payload"]["profile"] = pick(payload["profile"], PROFILE_FIELDS)
    else:
        visible["payload"] = pick(payload, ("exhibit_id", "draft_version", "assignment_command_id"))
        if isinstance(payload.get("content"), dict):
            visible["payload"]["content"] = pick(payload["content"], LABEL_FIELDS)
    if isinstance(value.get("recovery"), dict):
        visible["recovery"] = pick(value["recovery"], RECOVERY_FIELDS)
    return visible


class CursorCodec:
    """AES-GCM cursors bound to the current authorization scope and filtered query.

    The key is an independently generated IAM-only auth-control record, never
    the shared producer credential. HTTP routes cannot address that collection.
    Reads never create keys or cursor records.
    """
    def __init__(self, api, kind, query):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self.api = api
        person = auth.principal.get()
        if person["kind"] == "hardware":
            scope = {"policy": "hardware-command-reads-v1", "device_id": person["device_id"]}
        else:
            scope = {"policy": "staff-workflow-reads-v1", "issuer": person["issuer"],
                     "subject": person["subject"], "role": person["role"],
                     "account_version": int(person["account"]["version"])}
        scope.update(kind=kind, query={key: value for key, value in query.items() if key not in ("cursor", "limit")})
        self.aad = json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
        item = store.get(api, "auth-control", CURSOR_KEY_ID, required=False)
        try:
            if item["data"]["purpose"] != "staff-workflow-cursors-v1":
                raise ValueError()
            key = base64.b64decode(item["data"]["encryption_key"], altchars=b"-_", validate=True)
            if len(key) != 32:
                raise ValueError()
        except (TypeError, KeyError, ValueError):
            raise api.APIError(503, "service_unavailable", "Secure pagination is not configured.") from None
        self.cipher = AESGCM(key)

    def encode(self, last):
        now = int(self.api.now_utc().timestamp())
        payload = json.dumps({"key": last, "expires": now + CURSOR_SECONDS}, separators=(",", ":")).encode()
        # Fixed-size padding hides restricted index-key and identifier lengths.
        if len(payload) > 1022:
            raise self.api.APIError(503, "service_unavailable", "The continuation key is too large.")
        plaintext = len(payload).to_bytes(2, "big") + payload + os.urandom(1022 - len(payload))
        nonce = os.urandom(12)
        return base64.urlsafe_b64encode(nonce + self.cipher.encrypt(nonce, plaintext, self.aad)).decode()

    def decode(self, cursor):
        from cryptography.exceptions import InvalidTag

        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            if len(raw) != 12 + 1024 + 16:
                raise ValueError()
            plaintext = self.cipher.decrypt(raw[:12], raw[12:], self.aad)
            length = int.from_bytes(plaintext[:2], "big")
            if not 1 <= length <= 1022:
                raise ValueError()
            value = json.loads(plaintext[2:2 + length].decode())
            if set(value) != {"key", "expires"} or not isinstance(value["expires"], int):
                raise ValueError()
            now = int(self.api.now_utc().timestamp())
            if not now < value["expires"] <= now + CURSOR_SECONDS:
                raise ValueError()
            return value["key"]
        except (ValueError, UnicodeError, TypeError, InvalidTag):
            raise self.api.APIError(400, "invalid_query", "cursor is invalid for this query.") from None


def list_records(api, kind, query):
    person = auth.principal.get()
    if kind == "command" and person and person["kind"] == "hardware":
        return store.list_records(api, kind, query, cursor_codec=CursorCodec(api, kind, query))
    if not restricted(kind):
        return store.list_records(api, kind, query)
    return store.list_records(api, kind, query, project=lambda value: project(api, kind, value),
                              cursor_codec=CursorCodec(api, kind, query))
