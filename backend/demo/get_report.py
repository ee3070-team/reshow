#!/usr/bin/env python3
"""Retrieve an existing event or date-range report through the HTTPS API.
Print JSON or CSV and optionally save it; never upload or seed records.
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
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


def report_query(args):
    explicit = any(value is not None for value in (args.device_id, args.start, args.end))
    if explicit:
        if args.event_id is not None or not all((args.device_id, args.start, args.end)):
            raise ValueError("Choose --event-id OR all of --device-id, --start and --end.")
        query = {"device_id": args.device_id, "start": args.start, "end": args.end}
    else:
        query = {"event_id": args.event_id or "analytics-event-01"}
    if args.interval_minutes <= 0:
        raise ValueError("--interval-minutes must be positive.")
    return {**query, "interval_minutes": args.interval_minutes, "timezone": args.timezone, "format": args.format}


def run(client, query):
    _, report = client.request("/api/reports/event?" + urlencode(query), "GET")
    if query["format"] == "csv":
        if not isinstance(report, str):
            raise RuntimeError("Expected a CSV report.")
        return report
    return json.dumps(report, indent=2, allow_nan=False) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch an existing event report only. Never uploads data. Standard library only.")
    parser.add_argument("--event-id", help="Default: analytics-event-01")
    parser.add_argument("--device-id")
    parser.add_argument("--start", help="RFC3339 UTC timestamp ending in Z")
    parser.add_argument("--end", help="RFC3339 UTC timestamp ending in Z")
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--timezone", default="Asia/Hong_Kong")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--access-file", type=Path, default=Path(__file__).with_name("EE3070-ReShow-API-Access.txt"))
    parser.add_argument("--request-interval", type=float, default=1.1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        query = report_query(args)
        client = Client(*config(args.access_file), interval=args.request_interval)
        report = run(client, query)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report)
        print(report, end="")
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        # Exception messages come from local validation or sanitized HTTP statuses.
        if isinstance(exc, (ValueError, RuntimeError)) and str(exc).startswith(("HTTP 404:", "Choose --", "--interval-minutes")):
            print(str(exc), file=sys.stderr)
        else:
            print("Report failed. Check configuration, selection and service availability.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
