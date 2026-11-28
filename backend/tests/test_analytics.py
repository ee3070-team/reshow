"""Interval API and PDF 9/10 analytics contract, with atomic DynamoDB fake.

Expected durations are hand-calculated. These are engineering tests using
synthetic data, not hardware or human-study evidence.
"""

import copy
import csv
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_api as legacy
import analytics
import app


class TransactionFailure(Exception):
    def __init__(self, code="ConditionalCheckFailed"):
        self.response = {"Error": {"Code": "TransactionCanceledException"},
                         "CancellationReasons": [{"Code": code}, {"Code": "None"}]}


class AtomicTable(legacy.FakeTable):
    name = "test-readings"

    def __init__(self, page_size=250):
        super().__init__()
        self.meta = SimpleNamespace(client=self)
        self.page_size = page_size
        self.fail_transaction = None

    def transact_write_items(self, **kwargs):
        self.calls.append(("transact_write_items", copy.deepcopy(kwargs)))
        puts = [operation["Put"] for operation in kwargs["TransactItems"]]
        if len(puts) != 2:
            raise AssertionError("Expected canonical + timeline atomic pair")
        for operation in puts:
            if operation["TableName"] != self.name or operation["ConditionExpression"] != "attribute_not_exists(PK)":
                raise AssertionError("Immutable transaction required")
            item = operation["Item"]
            if (item["PK"], item["SK"]) in self.items:
                raise TransactionFailure()
        if self.fail_transaction:
            raise TransactionFailure(self.fail_transaction)
        staged = {(operation["Item"]["PK"], operation["Item"]["SK"]): copy.deepcopy(operation["Item"]) for operation in puts}
        self.items.update(staged)
        return {}

    def query(self, **kwargs):
        if "IndexName" in kwargs:
            return super().query(**kwargs)
        self.calls.append(("query", copy.deepcopy(kwargs)))
        if kwargs["KeyConditionExpression"] != "PK = :device" or kwargs["ConsistentRead"] is not True:
            raise AssertionError("Reports require strong base-table partition query")
        partition = kwargs["ExpressionAttributeValues"][":device"]
        items = sorted((copy.deepcopy(item) for item in self.items.values() if item["PK"] == partition), key=lambda item: item["SK"])
        if kwargs.get("ExclusiveStartKey"):
            previous = kwargs["ExclusiveStartKey"]
            items = [item for item in items if item["SK"] > previous["SK"]]
        limit = min(kwargs["Limit"], self.page_size)
        result = {"Items": items[:limit]}
        if len(items) > limit:
            last = items[limit - 1]
            result["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
        return result


class AnalyticsTests(unittest.TestCase):
    request = legacy.APITests.request
    create_exhibit = legacy.APITests.create_exhibit
    def setUp(self):
        legacy.APITests.setUp(self)
        self.table = AtomicTable(page_size=2)
        app._table = self.table
        fixed = patch.object(app, "now_utc", return_value=datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
        fixed.start()
        self.addCleanup(fixed.stop)
        self.query = {"device_id": "analytics-device", "start": "2026-10-25T00:00:00Z", "end": "2026-10-25T01:00:00Z"}
        self.base = {"device_id": "analytics-device", "start": self.query["start"], "end": self.query["end"], "source": "demo"}

    def post_ok(self, collection, payload):
        response, body = self.request("POST", "/api/" + collection, payload)
        self.assertEqual(response["statusCode"], 201, body)
        return body

    def fixture(self):
        self.create_exhibit()
        self.post_ok("events", {**self.base, "event_id": "event-1", "name": "Synthetic exhibition"})
        for record_id, left, right in (("carry-in", "2026-10-24T23:55:00Z", "2026-10-25T00:05:00Z"),
                                       ("event-b", "2026-10-25T00:03:00Z", "2026-10-25T00:10:00Z"),
                                       ("event-c", "2026-10-25T00:05:00Z", "2026-10-25T00:07:00Z")):
            self.post_ok("proximity-events", {**self.base, "proximity_event_id": record_id, "start": left, "end": right})
        for record_id, left, right in (("full-half", "00:00", "00:30"), ("partial", "00:45", "00:50")):
            self.post_ok("coverage", {**self.base, "coverage_id": record_id, "start": "2026-10-25T" + left + ":00Z",
                                      "end": "2026-10-25T" + right + ":00Z", "streams": list(analytics.STREAMS)})
        for index, left, right in ((1, "00:00", "00:30"), (2, "00:30", "01:00")):
            self.post_ok("display-history", {**self.base, "display_id": "display-" + str(index), "exhibit_id": self.exhibit["exhibit_id"],
                                             "start": "2026-10-25T" + left + ":00Z", "end": "2026-10-25T" + right + ":00Z"})
        self.post_ok("warnings", {**self.base, "warning_id": "warning-1", "exhibit_id": self.exhibit["exhibit_id"],
                                  "start": "2026-10-25T00:08:00Z", "end": "2026-10-25T00:12:00Z",
                                  "warning_type": "temperature", "message": "Producer reports persistent high temperature."})

    def test_hand_calculated_heatmap_quiet_missing_partial_and_overlap(self):
        self.fixture()
        result, body = self.request("GET", "/api/analytics/heatmap", query=self.query)
        self.assertEqual(result["statusCode"], 200, body)
        self.assertEqual([row["status"] for row in body["buckets"]], ["recorded_activity", "confirmed_quiet", "missing", "partial"])
        self.assertEqual([row["recorded_occupied_seconds"] for row in body["buckets"]], [600, 0, None, 0])
        self.assertEqual([row["proximity_event_count"] for row in body["buckets"]], [2, 0, None, 0])
        self.assertEqual([row["coverage_seconds"] for row in body["buckets"]], [900, 900, 0, 300])
        self.assertEqual(body["summary"]["recorded_occupied_seconds"], 600)
        self.assertEqual(body["summary"]["proximity_event_count"], 2)
        self.assertEqual(body["summary"]["missing_seconds"], 1500)
        self.assertEqual(body["summary"]["coverage_seconds"], 2100)
        self.assertEqual(body["summary"]["totals_interpretation"], "recorded_lower_bounds")
        self.assertEqual(len(body["missing_data"]["proximity"]), 2)
        self.assertEqual(body["buckets"][0]["local_start"], "2026-10-25T08:00:00+08:00")
        self.assertTrue(body["provenance"]["contains_demo_data"])
        queries = [params for operation, params in self.table.calls if operation == "query"]
        self.assertGreater(len(queries), 1)
        self.assertTrue(all(row["ConsistentRead"] for row in queries if "IndexName" not in row))
        self.assertTrue(any("ExclusiveStartKey" in row for row in queries))

    def test_event_and_explicit_selection_equal_and_report_includes_raw_history(self):
        self.fixture()
        _, report = self.request("GET", "/api/reports/event", query={"event_id": "event-1"})
        _, ranged = self.request("GET", "/api/reports/event", query=self.query)
        self.assertEqual(report["summary"], ranged["summary"])
        self.assertEqual(report["buckets"], ranged["buckets"])
        self.assertEqual(len(report["display_history"]), 2)
        self.assertEqual(len(report["environmental_warnings"]), 1)
        self.assertEqual(len(report["proximity_events"]), 3)
        self.assertEqual(report["proximity_events"][0]["clipped_duration_seconds"], 300)
        self.assertEqual(report["proximity_events"][0]["start"], "2026-10-24T23:55:00.000000Z")
        self.assertEqual(report["environmental_warnings"][0]["clipped_duration_seconds"], 240)
        self.assertEqual(report["environmental_warnings"][0]["source"], "demo")
        self.assertIn("producer", " ".join(report["limitations"]))
        self.assertEqual(report["event"]["name"], "Synthetic exhibition")

    def test_carryin_crosses_buckets_halfopen_end_boundaries_and_union_coverage(self):
        self.post_ok("proximity-events", {**self.base, "proximity_event_id": "crossing", "start": "2026-10-24T23:55:00Z", "end": "2026-10-25T00:20:00Z"})
        self.post_ok("proximity-events", {**self.base, "proximity_event_id": "ends-at-start", "start": "2026-10-24T23:50:00Z", "end": self.base["start"]})
        self.post_ok("proximity-events", {**self.base, "proximity_event_id": "starts-at-end", "start": self.base["end"], "end": "2026-10-25T01:10:00Z"})
        self.post_ok("coverage", {**self.base, "coverage_id": "one", "end": "2026-10-25T00:40:00Z", "streams": ["proximity"]})
        self.post_ok("coverage", {**self.base, "coverage_id": "two", "start": "2026-10-25T00:20:00Z", "streams": ["proximity"]})
        _, body = self.request("GET", "/api/analytics/heatmap", query=self.query)
        self.assertEqual([row["recorded_occupied_seconds"] for row in body["buckets"]], [900, 300, 0, 0])
        self.assertEqual(body["summary"]["proximity_event_count"], 0)
        self.assertEqual(body["summary"]["coverage_seconds"], 3600)
        self.assertEqual(body["summary"]["recorded_occupied_seconds"], 1200)
        self.assertEqual(body["missing_data"]["proximity"], [])
        self.assertEqual(body["data_completeness"]["warnings"]["missing_seconds"], 3600)

    def test_observations_without_coverage_are_lower_bounds_not_confirmed_zero(self):
        self.post_ok("proximity-events", {**self.base, "proximity_event_id": "positive", "start": "2026-10-25T00:02:00Z", "end": "2026-10-25T00:03:00Z"})
        _, body = self.request("GET", "/api/analytics/heatmap", query=self.query)
        self.assertEqual(body["buckets"][0]["status"], "partial")
        self.assertEqual(body["buckets"][0]["recorded_occupied_seconds"], 60)
        self.assertEqual(body["buckets"][0]["coverage_seconds"], 0)
        self.assertIsNone(body["buckets"][1]["proximity_event_count"])
        self.assertEqual(body["summary"]["missing_seconds"], 3600)

    def test_point_samples_do_not_create_duration_coverage_or_warnings(self):
        self.create_exhibit()
        self.request("POST", "/api/readings", dict(self.reading, device_id="analytics-device", temperature_c=100))
        _, body = self.request("GET", "/api/reports/event", query=self.query)
        self.assertTrue(all(row["status"] == "missing" for row in body["buckets"]))
        self.assertIsNone(body["summary"]["recorded_occupied_seconds"])
        self.assertEqual(body["environmental_warnings"], [])
        self.assertEqual(body["display_history"], [])
        self.assertTrue(all(value["missing_seconds"] == 3600 for value in body["data_completeness"].values()))

    def test_uploaded_occupancy_appears_in_activity_reports_and_csv_without_intervals(self):
        self.create_exhibit()
        for record_id, at, occupied in (("occupied-a", "2026-10-25T00:01:00Z", True),
                                        ("occupied-b", "2026-10-25T00:01:10.123Z", True),
                                        ("clear", "2026-10-25T00:01:20Z", False),
                                        ("end-excluded", self.query["end"], True)):
            self.post_ok("readings", dict(self.reading, reading_id=record_id,
                device_id="analytics-device", recorded_at=at, occupied=occupied))
        for path, query in (("/api/reports/event", self.query), ("/api/activity", {
                "device_id": "analytics-device", "start_date": "2026-10-25", "end_date": "2026-10-25"})):
            with patch.object(app, "now_utc", return_value=datetime(2026, 10, 25, 1, tzinfo=timezone.utc)):
                response, body = self.request("GET", path, query=query)
            self.assertEqual(response["statusCode"], 200, body)
            self.assertEqual(body["summary"]["occupied_sample_count"], 2)
            self.assertEqual(body["summary"]["sensor_sample_count"], 3)
            self.assertEqual([row["reading_id"] for row in body["sensor_samples"]], ["occupied-a", "occupied-b", "clear"])
            self.assertIsNone(body["summary"]["recorded_occupied_seconds"])
            self.assertIsNone(body["summary"]["proximity_event_count"])
            self.assertEqual(body["summary"]["coverage_seconds"], 0)
            self.assertEqual(sum(row["occupied_sample_count"] for row in body["buckets"]), 2)
        _, selected = self.request("GET", "/api/reports/event", query={**self.query, "exhibit_id": self.exhibit["exhibit_id"]})
        _, other = self.request("GET", "/api/reports/event", query={**self.query, "exhibit_id": "other-exhibit"})
        self.assertEqual(selected["summary"]["occupied_sample_count"], 2)
        self.assertEqual(other["sensor_samples"], [])
        response, _ = self.request("GET", "/api/reports/event", query={**self.query, "format": "csv"})
        self.assertIn("sensor_samples", response["body"])
        self.assertIn("occupied-a", response["body"])
        self.assertNotIn("end-excluded", response["body"])

    def test_sample_pagination_cap_and_nonadvancing_cursor(self):
        self.create_exhibit()
        for i in range(4):
            self.post_ok("readings", dict(self.reading, reading_id=f"sample-{i}", device_id="analytics-device",
                                        recorded_at=f"2026-10-25T00:01:0{i}Z"))
        original = self.table.query
        def paged(**kwargs):
            if "IndexName" in kwargs:
                kwargs["Limit"] = min(kwargs["Limit"], 2)
            return original(**kwargs)
        config = analytics.configuration(app, self.query)
        with patch.object(self.table, "query", side_effect=paged):
            samples = analytics.read_sensor_samples(app, "analytics-device", config[1], config[2])
            self.assertEqual(len(samples), 4)
            with patch.object(analytics, "MAX_SOURCE_RECORDS", 3), self.assertRaises(app.APIError) as error:
                analytics.read_sensor_samples(app, "analytics-device", config[1], config[2])
            self.assertEqual(error.exception.status, 413)
        with patch.object(self.table, "query", return_value={"Items": [], "LastEvaluatedKey": {"PK": "stuck"}}):
            with self.assertRaises(app.APIError) as error:
                analytics.read_sensor_samples(app, "analytics-device", config[1], config[2])
            self.assertEqual(error.exception.status, 503)

    def test_atomic_idempotency_normalization_conflict_and_exact_readback(self):
        payload = {**self.base, "coverage_id": "stable", "streams": ["warnings", "proximity"]}
        created = self.post_ok("coverage", payload)
        initial = copy.deepcopy(self.table.items)
        result, body = self.request("POST", "/api/coverage", dict(payload, streams=["proximity", "warnings"], start="2026-10-25T00:00:00.0Z"))
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(body["status"], "duplicate")
        self.assertEqual(self.table.items, initial)
        self.assertEqual(len(self.table.items), 2)
        result, body = self.request("GET", "/api/coverage/stable")
        self.assertEqual(body["item"], created["item"])
        self.assertEqual(body["consistency"], "strong")
        result, _ = self.request("POST", "/api/coverage", dict(payload, end="2026-10-25T00:59:00Z"))
        self.assertEqual(result["statusCode"], 409)
        self.assertEqual(self.table.items, initial)

    def test_transaction_failure_is_atomic_and_not_a_duplicate_success(self):
        self.table.fail_transaction = "ProvisionedThroughputExceeded"
        result, body = self.request("POST", "/api/proximity-events", {**self.base, "proximity_event_id": "fail"})
        self.assertEqual(result["statusCode"], 503)
        self.assertEqual(self.table.items, {})
        self.table.fail_transaction = None
        self.post_ok("proximity-events", {**self.base, "proximity_event_id": "fail"})
        self.assertEqual(len(self.table.items), 2)

    def test_history_cap_and_response_cap_explicit_failure_no_partial_success(self):
        self.fixture()
        with patch.object(analytics, "MAX_SOURCE_RECORDS", 3):
            result, body = self.request("GET", "/api/reports/event", query=self.query)
        self.assertEqual(result["statusCode"], 413)
        self.assertEqual(body["error"]["code"], "report_too_large")
        for output in ("json", "csv"):
            with patch.object(analytics, "MAX_RESPONSE_BYTES", 100):
                result, _ = self.request("GET", "/api/reports/event", query={**self.query, "format": output})
            self.assertEqual(result["statusCode"], 413)

    def test_exact_history_cap_accepts_empty_final_page(self):
        self.post_ok("coverage", {**self.base, "coverage_id": "cap-one", "streams": ["proximity"]})
        self.post_ok("coverage", {**self.base, "coverage_id": "cap-two", "streams": ["proximity"]})
        original = self.table.query

        def page_with_final_cursor(**params):
            result = original(**params)
            if result.get("Items") and not result.get("LastEvaluatedKey"):
                last = result["Items"][-1]
                result["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
            return result

        with patch.object(analytics, "MAX_SOURCE_RECORDS", 2), patch.object(self.table, "query", side_effect=page_with_final_cursor):
            result, body = self.request("GET", "/api/analytics/heatmap", query=self.query)
        self.assertEqual(result["statusCode"], 200, body)
        self.assertEqual(body["summary"]["coverage_seconds"], 3600)
        queries = [params for name, params in self.table.calls if name == "query" and "IndexName" not in params]
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[-1]["Limit"], 1)

    def test_warning_validation_and_display_overlap_diagnostic(self):
        self.create_exhibit()
        warning = {**self.base, "warning_id": "bad-warning", "exhibit_id": self.exhibit["exhibit_id"],
                   "warning_type": "temperature", "message": "reported"}
        for change in ({"warning_type": "humidity"}, {"message": None}, {"message": "x" * 1001}, {"end": None}):
            result, _ = self.request("POST", "/api/warnings", {**warning, **change})
            self.assertEqual(result["statusCode"], 400)
        for index in (1, 2):
            self.post_ok("display-history", {**self.base, "display_id": "overlap-" + str(index), "exhibit_id": self.exhibit["exhibit_id"]})
        _, body = self.request("GET", "/api/reports/event", query=self.query)
        self.assertTrue(body["data_quality"]["overlapping_display_intervals"])
        self.assertEqual(body["summary"]["warning_count_interpretation"], "recorded_lower_bounds")
        self.assertEqual(body["summary"]["display_history_interpretation"], "recorded_lower_bounds")

    def test_microsecond_durations_and_halfopen_bucket_counts(self):
        for record_id, left, right in (("fraction-a", "2026-10-25T00:00:00.100001Z", "2026-10-25T00:00:01.500003Z"),
                                       ("fraction-b", "2026-10-25T00:01:00Z", "2026-10-25T00:01:00.000001Z")):
            self.post_ok("proximity-events", {**self.base, "proximity_event_id": record_id, "start": left, "end": right})
        self.post_ok("coverage", {**self.base, "coverage_id": "micro-coverage", "streams": ["proximity"]})
        _, body = self.request("GET", "/api/analytics/heatmap", query={**self.query, "end": "2026-10-25T00:02:00Z", "interval_minutes": "1"})
        self.assertEqual([row["recorded_occupied_seconds"] for row in body["buckets"]], [1.400002, 0.000001])
        self.assertEqual([row["proximity_event_count"] for row in body["buckets"]], [1, 1])
        self.assertEqual(body["summary"]["recorded_occupied_seconds"], 1.400003)
        self.assertTrue(all(row["coverage_complete"] for row in body["buckets"]))

    def test_one_microsecond_missing_gap_cannot_be_confirmed_quiet(self):
        self.post_ok("coverage", {**self.base, "coverage_id": "micro-gap", "end": "2026-10-25T00:00:00.999999Z", "streams": ["proximity"]})
        self.post_ok("proximity-events", {**self.base, "proximity_event_id": "micro-carry", "start": "2026-10-24T23:59:59.999999Z", "end": "2026-10-25T00:00:00.000001Z"})
        _, body = self.request("GET", "/api/analytics/heatmap", query={**self.query, "end": "2026-10-25T00:00:01Z"})
        bucket = body["buckets"][0]
        self.assertEqual(bucket["recorded_occupied_seconds"], 0.000001)
        self.assertEqual(bucket["proximity_event_count"], 0)
        self.assertEqual(bucket["coverage_seconds"], 0.999999)
        self.assertFalse(bucket["coverage_complete"])
        self.assertEqual(bucket["status"], "partial")
        self.assertEqual(body["summary"]["missing_seconds"], 0.000001)
        self.assertEqual(body["missing_data"]["proximity"][0]["seconds"], 0.000001)

    def test_escape_heavy_valid_report_cannot_exceed_lambda_proxy_envelope(self):
        self.create_exhibit()
        self.table.page_size = 250
        for index in range(1500):
            self.post_ok("warnings", {**self.base, "warning_id": "escaped-" + str(index),
                "exhibit_id": self.exhibit["exhibit_id"], "warning_type": "temperature", "message": "\\" * 1000})
        result, body = self.request("GET", "/api/reports/event", query=self.query)
        self.assertEqual(result["statusCode"], 413, body)
        self.assertEqual(body["error"]["code"], "report_too_large")
        # A small CSV body can also expand after JSON encoding in Lambda.
        csv_result = {"statusCode": 200, "headers": {"content-type": "text/csv"}, "body": "\\" * 2_900_000}
        self.assertLess(len(csv_result["body"].encode()), analytics.MAX_RESPONSE_BYTES)
        with self.assertRaises(app.APIError) as error:
            analytics.bounded_proxy_response(app, csv_result)
        self.assertEqual(error.exception.status, 413)

    def test_record_validation_strict_fields_streams_dates_and_references(self):
        payload = {**self.base, "coverage_id": "invalid", "streams": ["proximity"]}
        invalid = [dict(payload, streams=value) for value in (None, [], ["proximity", "proximity"], ["unknown"], "proximity", [True], [{"bad": 1}])]
        invalid += [dict(payload, **change) for change in ({"start": payload["end"]}, {"end": "2026-10-27T01:00:00Z"},
                   {"end": "2026-11-26T01:00:00Z"}, {"start": "2026-02-30T00:00:00Z"}, {"source": None}, {"visitor_id": "no"}, {"device_id": True})]
        for data in invalid:
            with self.subTest(data=data):
                result, _ = self.request("POST", "/api/coverage", data)
                self.assertEqual(result["statusCode"], 400)
        self.assertEqual(self.table.items, {})
        result, _ = self.request("POST", "/api/display-history", {**self.base, "display_id": "no-exhibit", "exhibit_id": "missing"})
        self.assertEqual(result["statusCode"], 404)
        result, _ = self.request("POST", "/api/events", {**self.base, "event_id": "bad-name", "name": " "})
        self.assertEqual(result["statusCode"], 400)

    def test_query_validation_bounds_and_auth(self):
        invalid = [{}, {**self.query, "event_id": "mixed"}, {"event_id": "missing", "start": self.base["start"]},
                   {**self.query, "interval_minutes": "0"}, {**self.query, "interval_minutes": True}, {**self.query, "interval_minutes": "1441"},
                   {**self.query, "timezone": "Invalid/Zone"}, {**self.query, "timezone": "/etc/passwd"},
                   {**self.query, "format": "html"}, {**self.query, "unknown": "1"},
                   {**self.query, "start": "2026-09-01T00:00:00Z"},
                   {**self.query, "start": "2026-10-23T00:00:00Z", "interval_minutes": "1"}]
        for query in invalid:
            with self.subTest(query=query):
                result, _ = self.request("GET", "/api/analytics/heatmap", query=query)
                self.assertEqual(result["statusCode"], 400)
        for path in ("/api/events", "/api/proximity-events", "/api/coverage", "/api/display-history", "/api/warnings", "/api/analytics/heatmap", "/api/reports/event"):
            result, _ = self.request("GET", path, auth=False, query=self.query)
            self.assertEqual(result["statusCode"], 401)
        result, _ = self.request("GET", "/api/reports/event", query={"event_id": "missing"})
        self.assertEqual(result["statusCode"], 404)

    def test_daylight_saving_labels_have_offsets_and_elapsed_bucket_durations(self):
        # US autumn transition repeats 01:00 locally. UTC intervals stay 60 min
        # and their labels disambiguate the two occurrences with -04/-05.
        fixed = datetime(2026, 11, 2, tzinfo=timezone.utc)
        query = {"device_id": "dst", "start": "2026-11-01T05:00:00Z", "end": "2026-11-01T07:00:00Z", "interval_minutes": "60", "timezone": "America/New_York"}
        with patch.object(app, "now_utc", return_value=fixed):
            result, body = self.request("GET", "/api/analytics/heatmap", query=query)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual([row["local_start"] for row in body["buckets"]], ["2026-11-01T01:00:00-04:00", "2026-11-01T01:00:00-05:00"])
        self.assertEqual(body["data_completeness"]["proximity"]["missing_seconds"], 7200)

    def test_csv_download_preserves_missing_cells_and_report_warnings(self):
        self.fixture()
        result, body = self.request("GET", "/api/reports/event", query={**self.query, "format": "csv"})
        self.assertEqual(result["statusCode"], 200)
        self.assertIn("attachment", result["headers"]["content-disposition"])
        rows = list(csv.DictReader(io.StringIO(body)))
        buckets = [row for row in rows if row["section"] == "heatmap"]
        self.assertEqual(buckets[2]["recorded_occupied_seconds"], "")
        self.assertEqual(float(buckets[1]["recorded_occupied_seconds"]), 0)
        self.assertEqual(len([row for row in rows if row["section"] == "environmental_warnings"]), 1)
        self.assertTrue(any(row["section"] == "limitation" for row in rows))
        # Every column, including future free text columns, passes the sanitizer.
        fixture = {"device_id": "=cmd", "start": "x", "end": "y", "summary": {"proximity_event_count": None,
                   "recorded_occupied_seconds": None, "coverage_seconds": 0}, "provenance": {}, "event": None,
                   "timezone": "UTC", "buckets": [], "missing_data": {}, "limitations": ["\t=malicious", "@formula"]}
        exported = analytics.csv_response(app, fixture, {})
        sanitized = list(csv.DictReader(io.StringIO(exported["body"])))
        self.assertEqual(sanitized[0]["device_id"], "'=cmd")
        self.assertEqual(sanitized[1]["detail"], "'\t=malicious")
        self.assertEqual(sanitized[2]["detail"], "'@formula")


if __name__ == "__main__":
    unittest.main()
