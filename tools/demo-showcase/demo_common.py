"""Standard-library client for the reserved ReShow demo-test namespace."""
import base64
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://urauw4ckw1.execute-api.us-east-1.amazonaws.com"
PREFIX = "demo-test-"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def observation_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise ValueError("Observation time must be an RFC3339 UTC timestamp.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Observation time cannot be more than five minutes in the future.")
    return parsed


def scoped(value):
    if not isinstance(value, str) or not value.startswith(PREFIX) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Only demo-test-* identifiers are permitted.")
    return value


def save(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing a symlink output.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def read_private(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Credential inputs must be private regular files (chmod 600).")
    return json.loads(path.read_text())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, token, interval=1.1):
        if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError("Invalid private API credential.")
        if not math.isfinite(interval) or interval < 1:
            raise ValueError("Use a request interval of at least one second.")
        self.token, self.interval = token, interval
        self.last = 0
        self.calls = []
        self.opener = build_opener(NoRedirect())

    def request(self, path, method="GET", payload=None, expected=(200,), headers=None):
        if not path.startswith(("/api/", "/staff-api/")) or "\n" in path or "\r" in path:
            raise ValueError("Invalid API path.")
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        request = Request(API + path, data=body, method=method, headers={
            "Accept": "application/json, text/csv", "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token, **(headers or {})})
        for attempt in range(4):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                with self.opener.open(request, timeout=30) as result:
                    status, mime, raw = result.status, result.headers.get("Content-Type", ""), result.read(4_100_000)
            except HTTPError as exc:
                status, mime, raw = exc.code, exc.headers.get("Content-Type", ""), exc.read(4_100_000)
            except (OSError, URLError):
                # A write may have succeeded. Resume with the same manifest to reconcile.
                raise RuntimeError("Request interrupted; retry unchanged manifest/IDs.") from None
            if method == "GET" and status in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            self.calls.append({"method": method, "path": path.split("?")[0], "http_status": status})
            if status not in expected:
                code = "unknown"
                try:
                    code = json.loads(raw).get("error", {}).get("code", code)
                except (ValueError, AttributeError):
                    pass
                raise RuntimeError(f"HTTP {status} ({code}) for {method} {path.split('?')[0]}; no overwrite attempted.")
            return status, json.loads(raw) if "application/json" in mime else raw.decode()
        raise RuntimeError("Read retries exhausted.")

    @staticmethod
    def s3(url):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or not (
                host.endswith(".s3.amazonaws.com") or ".s3." in host and host.endswith(".amazonaws.com")):
            raise ValueError("Expected an AWS S3 HTTPS capability.")
        return url

    def upload_png(self, form):
        boundary = "reshow-demo-test-upload"
        fields = []
        for key, value in form["fields"].items():
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or "\r" in value or "\n" in value:
                raise ValueError("Invalid S3 form field.")
            fields.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
        fields.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="demo.png"\r\nContent-Type: image/png\r\n\r\n').encode(),
                       PNG, f"\r\n--{boundary}--\r\n".encode()])
        request = Request(self.s3(form["url"]), data=b"".join(fields), method="POST",
                          headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        try:
            with self.opener.open(request, timeout=30) as result:
                if result.status not in (200, 201, 204):
                    raise RuntimeError("Private image upload failed.")
        except (HTTPError, URLError, OSError):
            raise RuntimeError("Private image upload failed; capability was not logged.") from None

    def download_png(self, download):
        try:
            with self.opener.open(self.s3(download["url"]), timeout=30) as result:
                return result.read(1_048_577)
        except (HTTPError, URLError, OSError):
            raise RuntimeError("Private image download failed; capability was not logged.") from None


def clients(admin_tokens, producer_access):
    admin_value, producer = read_private(admin_tokens), read_private(producer_access)
    admin = Client(admin_value["tokens"]["access_token"])
    if producer.get("api_base_url") != API:
        raise ValueError("Producer credential targets another API.")
    machine = Client(producer["bearer_token"])
    return admin, machine


def require_admin(admin):
    user = admin.request("/staff-api/session")[1]["user"]
    if user.get("staff_id") != "ee3070-staff" or user.get("role") != "Administrator":
        raise ValueError("Sign in as the existing test@example.com Administrator.")
    return {"staff_id": user["staff_id"], "role": user["role"]}


def subset(actual, expected):
    return all(key in actual and actual[key] == value for key, value in expected.items())


PNG_SHA256 = hashlib.sha256(PNG).hexdigest()
