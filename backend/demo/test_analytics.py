#!/usr/bin/env python3
"""Upload and verify synthetic PDF No. 9/10 backend data using HTTPS only.

Standard library only. Use API_BASE_URL and API_TOKEN together, or keep the
private EE3070-ReShow-API-Access.txt next to this script (--access-file also
works). No AWS login or hardware is needed. The default fixture has stable IDs:
rerunning unchanged input verifies duplicates without adding activity. Use an
optional --run-id to isolate an additional synthetic run. Never prints tokens.
"""

import argparse
import copy
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
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
        if interval < 1:
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
                raise RuntimeError(f"Unexpected HTTP {status} for {method} {path.split('?')[0]}.")
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


def normalized(payload):
    value = copy.deepcopy(payload)
    for key in ("start", "end", "recorded_at"):
        if key in value:
            value[key] = datetime.fromisoformat(value[key].replace("Z", "+00:00")).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if "streams" in value:
        value["streams"] = sorted(value["streams"])
    return value


def fixture_path():
    root = Path(__file__).parent
    for name in ("analytics_fixture.json", "EE3070-ReShow-analytics-test-data.json"):
        if (root / name).is_file():
            return root / name
    return root / "analytics_fixture.json"


def prepare_fixture(fixture, run_id):
    value = copy.deepcopy(fixture)
    if run_id:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,11}", run_id):
            raise ValueError("--run-id must be 1-12 identifier characters, starting with a letter or digit.")
        for records in value.values():
            if not isinstance(records, list):
                continue
            for row in records:
                if isinstance(row, dict):
                    for key in row:
                        if key.endswith("_id"):
                            row[key] += "-" + run_id
        value["query"]["event_id"] += "-" + run_id
    return value


def run(client, fixture):
    checks, uploads = [], []

    def check(label, predicate):
        if not predicate:
            raise RuntimeError("Verification failed: " + label)
        checks.append({"check": label, "passed": True})

    # Completion declarations must follow every underlying record upload.
    collections = [("exhibits", "exhibit_id"), ("events", "event_id"),
                   ("proximity-events", "proximity_event_id"), ("display-history", "display_id"),
                   ("warnings", "warning_id"), ("coverage", "coverage_id")]
    for collection, id_key in collections:
        for payload in fixture[collection]:
            status, created = client.request("/api/" + collection, "POST", payload, expected=(200, 201))
            exact_path = "/api/" + collection + "/" + quote(payload[id_key], safe="")
            _, stored = client.request(exact_path)
            item = dict(stored.get("item", {}))
            received_at = item.pop("received_at", None)
            check("exact readback " + collection + "/" + payload[id_key],
                  stored.get("consistency") == "strong" and item == normalized(payload)
                  and bool(received_at) and created.get("status") == ("created" if status == 201 else "duplicate"))
            uploads.append({"collection": collection, "http_status": status, "status": created["status"],
                            "exact_readback": stored})
    expected = fixture["expected"]
    query = dict(fixture["query"])
    _, heatmap = client.request("/api/analytics/heatmap?" + urlencode(query))
    _, report = client.request("/api/reports/event?" + urlencode(query))
    check("strong analytics read", heatmap["consistency"] == report["consistency"] == "strong")
    check("four distinct bucket states", [b["status"] for b in heatmap["buckets"]] == expected["bucket_statuses"])
    check("clipped union duration and unknown bucket", [b["recorded_occupied_seconds"] for b in heatmap["buckets"]] == expected["bucket_occupied_seconds"])
    check("event starts exclude carry-in and end boundary", [b["proximity_event_count"] for b in heatmap["buckets"]] == expected["bucket_event_starts"])
    check("per-bucket producer coverage", [b["coverage_seconds"] for b in heatmap["buckets"]] == expected["bucket_coverage_seconds"])
    summary = report["summary"]
    check("exact summary math", summary["proximity_event_count"] == expected["event_starts"]
          and summary["recorded_occupied_seconds"] == expected["occupied_seconds"]
          and summary["coverage_seconds"] == expected["coverage_seconds"]
          and summary["missing_seconds"] == expected["missing_seconds"]
          and not summary["coverage_complete"] and summary["totals_interpretation"] == "recorded_lower_bounds"
          and summary["warning_count_interpretation"] == "recorded_lower_bounds"
          and summary["display_history_interpretation"] == "recorded_lower_bounds")
    check("local labels use selected timezone", heatmap["buckets"][0]["local_start"] == "2026-10-25T08:00:00+08:00")
    for stream in ("proximity", "display", "warnings"):
        complete = report["data_completeness"][stream]
        actual_missing = [{"start": r["start"], "end": r["end"]} for r in report["missing_data"][stream]]
        check("explicit missing periods " + stream,
              complete["coverage_seconds"] == expected["coverage_seconds"]
              and complete["missing_seconds"] == expected["missing_seconds"] and not complete["complete"]
              and actual_missing == [normalized(r) for r in expected["missing_periods"]])
    check("carry-in interval retained", len(report["proximity_events"]) == expected["overlapping_events"]
          and report["proximity_events"][0]["clipped_duration_seconds"] == 300)
    check("two exhibit display histories", len(report["display_history"]) == 2
          and len({r["exhibit_id"] for r in report["display_history"]}) == 2
          and sum(r["clipped_duration_seconds"] for r in report["display_history"]) == expected["display_seconds"])
    check("warning history duration", len(report["environmental_warnings"]) == expected["warning_count"]
          and sum(r["clipped_duration_seconds"] for r in report["environmental_warnings"]) == expected["warning_seconds"])
    check("synthetic provenance and limitations", report["provenance"] == {"sources": ["demo"], "contains_demo_data": True}
          and any("not unique visitors" in value for value in report["limitations"]))
    event = fixture["events"][0]
    direct_query = {"device_id": event["device_id"], "start": event["start"], "end": event["end"],
                    "interval_minutes": 15, "timezone": "Asia/Hong_Kong"}
    _, direct = client.request("/api/reports/event?" + urlencode(direct_query))
    check("event and explicit date-range selection agree", direct["event"] is None
          and direct["summary"] == summary and direct["buckets"] == report["buckets"])
    exports = {}
    for name, path in (("heatmap", "/api/analytics/heatmap"), ("event_report", "/api/reports/event")):
        _, raw = client.request(path + "?" + urlencode({**query, "format": "csv"}))
        rows = list(csv.DictReader(io.StringIO(raw)))
        buckets = [row for row in rows if row["section"] == "heatmap"]
        check(name + " CSV preserves states and unknown values", len(buckets) == 4
              and [row["status"] for row in buckets] == expected["bucket_statuses"]
              and [float(row["recorded_occupied_seconds"]) if row["recorded_occupied_seconds"] else None for row in buckets] == [600, 0, None, 0])
        if name == "event_report":
            check("CSV includes all report sections", {"summary", "heatmap", "proximity_events", "display_history",
                  "environmental_warnings", "coverage_records", "missing_data_proximity", "missing_data_display",
                  "missing_data_warnings", "limitation"}.issubset({row["section"] for row in rows}))
        exports[name] = raw
    duplicate_payload = fixture["proximity-events"][0]
    _, duplicate = client.request("/api/proximity-events", "POST", duplicate_payload)
    check("idempotent duplicate", duplicate["status"] == "duplicate")
    _, conflict = client.request("/api/proximity-events", "POST", {**duplicate_payload, "source": "device"}, expected=(409,))
    check("changed payload conflict", conflict["error"]["code"] == "id_conflict")
    _, unchanged = client.request("/api/proximity-events/" + quote(duplicate_payload["proximity_event_id"], safe=""))
    check("conflict preserves original", {k: v for k, v in unchanged["item"].items() if k != "received_at"} == normalized(duplicate_payload))
    negatives = [
        ("analytics requires authorization", "/api/analytics/heatmap?" + urlencode(query), None, False, 401),
        ("report requires authorization", "/api/reports/event?" + urlencode(query), None, False, 401),
        ("mixed selectors rejected", "/api/reports/event?" + urlencode({**query, "device_id": event["device_id"]}), None, True, 400),
        ("unknown timezone rejected", "/api/analytics/heatmap?" + urlencode({**query, "timezone": "Invalid/Timezone"}), None, True, 400),
        ("zero interval rejected", "/api/analytics/heatmap?" + urlencode({**query, "interval_minutes": 0}), None, True, 400),
        ("unknown input field rejected", "/api/events", {**event, "unsupported": True}, True, 400),
        ("zero duration rejected", "/api/events", {**event, "end": event["start"]}, True, 400),
    ]
    for label, path, payload, auth, expected_status in negatives:
        status, _ = client.request(path, "POST" if payload else "GET", payload, expected=(expected_status,), auth=auth)
        check(label, status == expected_status)
    return {"data_kind": "synthetic_demo", "hardware_connected": False,
            "completed_at": datetime.now(timezone.utc).isoformat(), "fixture_event_id": query["event_id"],
            "device_id": event["device_id"], "verified_record_count": len(uploads),
            "passed_checks": len(checks), "checks": checks, "uploads": uploads,
            "heatmap": heatmap, "event_report": report, "csv_exports": exports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=fixture_path())
    parser.add_argument("--access-file", type=Path, default=Path(__file__).with_name("EE3070-ReShow-API-Access.txt"))
    parser.add_argument("--run-id", help="Optional fresh 1-12 character suffix; reuse it to retry unchanged records")
    parser.add_argument("--request-interval", type=float, default=1.1)
    parser.add_argument("--output", type=Path, help="Save token-free JSON verification evidence")
    args = parser.parse_args()
    try:
        data = prepare_fixture(json.loads(args.fixture.read_text()), args.run_id)
        client = Client(*config(args.access_file), interval=args.request_interval)
        evidence = run(client, data)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
        print(f"PASS: {evidence['passed_checks']} checks; {evidence['verified_record_count']} synthetic records read back exactly.")
        print("Heatmap: activity / quiet / missing / partial; occupied=600s, starts=2, covered=2100s, missing=1500s.")
        if args.output:
            print("Token-free evidence: " + str(args.output))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print("Analytics test failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
