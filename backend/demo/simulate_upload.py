#!/usr/bin/env python3
"""Upload synthetic ReShow records through HTTPS and verify exact API readback.

Use API_BASE_URL and API_TOKEN environment variables, or --ssm-config on the
dedicated EC2 instance to retrieve them via its IAM role. Never prints the token.
Only Python's standard library is required; --ssm-config also needs AWS CLI.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward a Bearer token to a redirected destination.


class DemoClient:
    def __init__(self, base_url, token, request_interval=1.1):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("API_BASE_URL must be an HTTPS URL without credentials, query or fragment.")
        if len(token) < 32 or any(char.isspace() for char in token):
            raise ValueError("API_TOKEN must contain at least 32 characters without whitespace.")
        if request_interval < 1:
            raise ValueError("Use a request interval of at least one second.")
        self.base_url, self.token = base_url.rstrip("/"), token
        self.request_interval, self.last_request = request_interval, 0
        self.opener = build_opener(NoRedirect())

    def request(self, path, method="GET", payload=None):
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        for attempt in range(4):
            time.sleep(max(0, self.request_interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with self.opener.open(request, timeout=20) as result:
                    return json.loads(result.read(131072))
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("API request failed with HTTP " + str(exc.code) + "; no record was counted as verified.") from None
            except (URLError, TimeoutError, OSError):
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("HTTPS request failed; retry with the same reading IDs if checking a partial run.") from None


def ssm_config(region):
    def value(name, secret=False):
        args = ["aws", "ssm", "get-parameter", "--region", region, "--name", name,
                "--query", "Parameter.Value", "--output", "text"]
        if secret:
            args.append("--with-decryption")
        result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError("Unable to retrieve demo API configuration from SSM.")
        return result.stdout.strip()
    return value("/ee3070/reshow/demo/api-base-url"), value("/ee3070/reshow/demo/api-token", secret=True)


def demo_exhibit(exhibit_id):
    return {"exhibit_id": exhibit_id, "name": "ReShow demonstration artifact",
            "description": "Synthetic demonstration profile.", "tag_id": "DEMO-TAG-001",
            "temperature_min_c": 18, "temperature_max_c": 28,
            "brightness_min_raw": 600, "brightness_max_raw": 3000}


def make_readings(count, device_id, exhibit_id, run_id, start=None):
    start = start or datetime.now(timezone.utc) - timedelta(minutes=count - 1)
    for index in range(count):
        occupied = index % 4 != 0
        yield {"reading_id": run_id + "-" + str(index + 1).zfill(3), "device_id": device_id,
               "exhibit_id": exhibit_id, "recorded_at": (start + timedelta(minutes=index)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               "temperature_c": round(22.2 + math.sin(index / 2) * 1.2, 1),
               "brightness_raw": 1750 + index * 12 if occupied else 620 + index * 6,
               "occupied": occupied, "distance_cm": 65 + index % 12 * 3 if occupied else None,
               "source": "demo"}


def verify_readback(expected, result):
    if result.get("consistency") != "strong" or not isinstance(result.get("item"), dict):
        raise RuntimeError("Exact strongly consistent readback was not returned.")
    stored = result["item"]
    for key, value in expected.items():
        actual = stored.get(key)
        if key == "recorded_at":
            actual = datetime.fromisoformat(actual.replace("Z", "+00:00")) if isinstance(actual, str) else None
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if actual != value or (isinstance(value, bool) and type(actual) is not bool):
            raise RuntimeError("Stored payload mismatch for field " + key + ".")
    if not stored.get("received_at"):
        raise RuntimeError("Stored record is missing the server receive timestamp.")
    return stored


def run_demo(client, readings, exhibit):
    exhibit_result = client.request("/api/exhibits", "POST", exhibit)
    exhibit_readback = client.request("/api/exhibits/" + quote(exhibit["exhibit_id"], safe=""))
    verify_readback(exhibit, exhibit_readback)
    evidence = {"data_kind": "synthetic_demo", "hardware_connected": False,
                "exhibit_status": exhibit_result.get("status"), "exhibit": exhibit_readback["item"],
                "verified_count": 0, "readings": []}
    for reading in readings:
        uploaded = client.request("/api/readings", "POST", reading)
        readback = client.request("/api/readings/" + quote(reading["reading_id"], safe=""))
        stored = verify_readback(reading, readback)
        evidence["readings"].append({"upload_status": uploaded.get("status"), "exact_readback": stored,
                                    "consistency": readback["consistency"]})
        evidence["verified_count"] += 1
    evidence["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssm-config", action="store_true", help="Read API URL and token using the EC2 IAM role and AWS CLI")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--device-id", default="ec2-demo-01")
    parser.add_argument("--exhibit-id", default="demo-artifact-01")
    parser.add_argument("--request-interval", type=float, default=1.1, help="Seconds between HTTPS requests; minimum 1")
    parser.add_argument("--input", type=Path, help="Retry an existing JSON array of reading payloads with their original IDs and timestamps")
    parser.add_argument("--save-input", type=Path, help="Save generated payloads before uploading, for safe retries")
    parser.add_argument("--output", type=Path, help="Write token-free verification evidence JSON here")
    args = parser.parse_args()
    try:
        if not 1 <= args.count <= 100:
            raise ValueError("--count must be between 1 and 100.")
        for value in (args.device_id, args.exhibit_id):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
                raise ValueError("Device and exhibit IDs must use 1-64 identifier characters.")
        base_url, token = ssm_config(args.region) if args.ssm_config else (os.environ.get("API_BASE_URL", ""), os.environ.get("API_TOKEN", ""))
        client = DemoClient(base_url, token, args.request_interval)
        readings = json.loads(args.input.read_text()) if args.input else list(make_readings(args.count, args.device_id, args.exhibit_id, "demo-" + uuid.uuid4().hex[:16]))
        if not isinstance(readings, list) or not 1 <= len(readings) <= 100:
            raise ValueError("--input must contain an array of 1-100 reading payloads.")
        if args.save_input:
            args.save_input.parent.mkdir(parents=True, exist_ok=True)
            args.save_input.write_text(json.dumps(readings, indent=2) + "\n")
        evidence = run_demo(client, readings, demo_exhibit(args.exhibit_id))
        rendered = json.dumps(evidence, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        print(rendered, end="")
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print("Demo failed: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
