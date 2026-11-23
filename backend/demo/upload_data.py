#!/usr/bin/env python3
"""Upload demo or device records sequentially through the HTTPS API.
Preflight IDs and duplicates before sending any record; never fetch reports.
"""
import argparse
import copy
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, token, interval=1.1):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("API base URL must use HTTPS without credentials, query or fragment.")
        if len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError("API token must have at least 32 characters without whitespace.")
        if not math.isfinite(interval) or interval < 1:
            raise ValueError("Request interval must be at least one second.")
        self.base_url, self.token, self.interval = base_url.rstrip("/"), token, interval
        self.last_request = 0
        self.opener = build_opener(NoRedirect())

    def request(self, path, method="GET", payload=None, expected=(200,), auth=True):
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        headers = {"Accept": "application/json, text/csv"}
        if auth:
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        for attempt in range(4):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with self.opener.open(request, timeout=30) as result:
                    status, content_type = result.status, result.headers.get("Content-Type", "")
                    raw = result.read(4_100_000).decode("utf-8")
            except HTTPError as exc:
                status, content_type = exc.code, exc.headers.get("Content-Type", "")
                raw = exc.read(4_100_000).decode("utf-8")
                if status in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
            except (URLError, TimeoutError, OSError):
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("HTTPS request failed. Retry unchanged IDs/payloads to recover an interrupted run.") from None
            if status not in expected:
                if status == 409:
                    raise RuntimeError("HTTP 409: this ID already has different data; the stored item was not overwritten.")
                if status == 404:
                    raise RuntimeError("HTTP 404: event not found; upload it first or choose an existing event.")
                raise RuntimeError(f"Unexpected HTTP {status}.")
            if "application/json" in content_type:
                return status, json.loads(raw)
            if "text/csv" in content_type:
                return status, raw
            raise RuntimeError("API returned an unexpected content type.")
        raise RuntimeError("Request retries exhausted.")


def config(access_file):
    base_url, token = os.environ.get("API_BASE_URL", ""), os.environ.get("API_TOKEN", "")
    if base_url or token:
        if not base_url or not token:
            raise ValueError("Set API_BASE_URL and API_TOKEN together, or unset both to use the private access file.")
        return base_url, token
    if not access_file.is_file():
        raise ValueError("Set API_BASE_URL/API_TOKEN or supply --access-file pointing to the private access file.")
    values = {}
    for line in access_file.read_text().splitlines():
        name, separator, value = line.partition(":")
        if separator:
            values[name.strip().lower()] = value.strip()
    return values.get("api base url", ""), values.get("bearer token", "")


COLLECTIONS = (("exhibits", "exhibit_id"), ("events", "event_id"),
               ("readings", "reading_id"), ("proximity-events", "proximity_event_id"),
               ("display-history", "display_id"), ("warnings", "warning_id"),
               ("coverage", "coverage_id"))
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def fixture_path():
    for name in ("analytics_fixture.json", "EE3070-ReShow-analytics-test-data.json"):
        path = Path(__file__).with_name(name)
        if path.is_file():
            return path
    return Path(__file__).with_name("analytics_fixture.json")


def prepare_uploads(data, collection=None, run_id=None):
    """Validate every record and duplicate before allowing any HTTP requests."""
    if run_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,11}", run_id):
        raise ValueError("--run-id must be 1-12 identifier characters, starting with a letter or digit.")
    if collection:
        if not isinstance(data, list):
            raise ValueError("--collection requires a JSON array of records.")
        data = {collection: data}
    if not isinstance(data, dict):
        raise ValueError("Input must be a grouped JSON object, or an array with --collection.")
    allowed_keys = {name for name, _ in COLLECTIONS} | {
        "data_kind", "hardware_connected", "description", "query", "expected"}
    if set(data) - allowed_keys:
        raise ValueError("Input contains an unknown collection or metadata field; no records have been uploaded.")
    if not any(name in data for name, _ in COLLECTIONS):
        raise ValueError("Input has no supported collections.")
    seen, uploads, skipped = {}, [], 0
    for name, id_key in COLLECTIONS:
        records = data.get(name, [])
        if not isinstance(records, list):
            raise ValueError("Each collection must be an array of records.")
        for original in records:
            if not isinstance(original, dict):
                raise ValueError("Each record must be a JSON object.")
            row = copy.deepcopy(original)
            if id_key not in row:
                raise ValueError("Every record requires its collection's ID field.")
            for key, value in list(row.items()):
                if key.endswith("_id"):
                    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                        raise ValueError("IDs must be 1-64 letters, digits, underscores or hyphens, starting with a letter or digit.")
                    if run_id:
                        row[key] = value + "-" + run_id
                        if not IDENTIFIER.fullmatch(row[key]):
                            raise ValueError("An ID exceeds 64 characters after adding --run-id.")
            try:
                canonical = json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":"))
            except (ValueError, TypeError):
                raise ValueError("Records must contain valid finite JSON values.") from None
            key = (name, row[id_key])
            if key in seen:
                if seen[key] != canonical:
                    raise ValueError("Conflicting duplicate ID in input; no records have been uploaded.")
                skipped += 1
                continue
            seen[key] = canonical
            uploads.append((name, row))
    return uploads, skipped


def run(client, uploads, skipped=0):
    results = []
    id_fields = dict(COLLECTIONS)
    for collection, payload in uploads:
        status, acknowledgment = client.request("/api/" + collection, "POST", payload, expected=(200, 201))
        results.append({"collection": collection, "id": payload[id_fields[collection]],
                        "http_status": status, "status": acknowledgment.get("status")})
    return {"uploaded_unique_records": len(results), "skipped_identical_input_duplicates": skipped,
            "created": sum(r["http_status"] == 201 for r in results),
            "already_stored": sum(r["http_status"] == 200 for r in results), "uploads": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Upload records only, sequentially. Stable IDs make unchanged retries safe. Standard library only.")
    parser.add_argument("--fixture", "--input", dest="fixture", type=Path, default=fixture_path())
    parser.add_argument("--collection", choices=[name for name, _ in COLLECTIONS], help="Collection for a raw JSON array")
    parser.add_argument("--run-id", help="Optional suffix for all IDs and references; reuse for retries")
    parser.add_argument("--access-file", type=Path, default=Path(__file__).with_name("EE3070-ReShow-API-Access.txt"))
    parser.add_argument("--request-interval", type=float, default=1.1)
    parser.add_argument("--output", type=Path, help="Optional token-free JSON acknowledgment file")
    args = parser.parse_args(argv)
    client = None
    try:
        uploads, skipped = prepare_uploads(json.loads(args.fixture.read_text()), args.collection, args.run_id)
        client = Client(*config(args.access_file), interval=args.request_interval)
        result = json.dumps(run(client, uploads, skipped), indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result)
        print(result, end="")
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        # Avoid echoing file contents, bearer tokens, URLs or arbitrary server text.
        if str(exc).startswith(("HTTP 409:", "Conflicting duplicate ID", "Every record requires", "IDs must", "An ID exceeds", "Each record must", "Input must", "--collection requires", "Input has no", "Input contains an unknown", "Each collection must", "Records must", "--run-id must")):
            print(str(exc), file=sys.stderr)
        else:
            print("Upload failed. Check input/configuration. Retry unchanged IDs after network errors.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
