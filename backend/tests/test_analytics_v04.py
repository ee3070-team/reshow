"""Hand-calculated v0.4 attribution/cutoff regressions using synthetic intervals."""

import copy
import csv
import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import test_analytics as original
import test_media_reports as media_tests
import analytics
import app
import auth
import media_reports


class AttributionTests(unittest.TestCase):
    request = original.AnalyticsTests.request
    create_exhibit = original.AnalyticsTests.create_exhibit
    post_ok = original.AnalyticsTests.post_ok
    setUp = original.AnalyticsTests.setUp

    def interval(self, kind, identity, start, end, **values):
        field = {"display-history": "display_id", "proximity-events": "proximity_event_id", "coverage": "coverage_id", "warnings": "warning_id"}[kind]
        return self.post_ok(kind, {**self.base, field: identity, "start": start, "end": end, **values})

    @staticmethod
    def at(value):
        return "2026-10-25T" + value + ":00Z"

    def display(self, identity, exhibit, left, right):
        return self.interval("display-history", identity, self.at(left), self.at(right), exhibit_id=exhibit)

    def proximity(self, identity, left, right):
        return self.interval("proximity-events", identity, self.at(left), self.at(right))

    def full_coverage(self):
        self.interval("coverage", "all", self.query["start"], self.query["end"], streams=list(analytics.STREAMS))

    def two_exhibits(self):
        self.create_exhibit()
        self.a = self.exhibit["exhibit_id"]
        self.b = "exhibit-b"
        result, payload = self.request("POST", "/api/exhibits", {**self.exhibit, "exhibit_id": self.b, "tag_id": "TAG-B"})
        self.assertEqual(result["statusCode"], 201, payload)

    def report(self, exhibit=None, **query):
        selected = {**self.query, **query}
        if exhibit is not None:
            selected["exhibit_id"] = exhibit
        response, payload = self.request("GET", "/api/reports/event", query=selected)
        self.assertEqual(response["statusCode"], 200, payload)
        return payload

    def switch_fixture(self):
        self.two_exhibits()
        self.display("a-first", self.a, "00:00", "00:20")
        self.display("b-middle", self.b, "00:20", "00:40")
        self.display("a-last", self.a, "00:40", "01:00")
        self.interval("proximity-events", "carry", "2026-10-24T23:55:00Z", self.at("00:10"))
        self.proximity("cross", "00:10", "00:30")
        self.proximity("overlap", "00:15", "00:25")
        self.proximity("starts-on-b", "00:30", "00:45")
        self.proximity("inside-b", "00:35", "00:38")
        self.interval("warnings", "a-warning", self.at("00:05"), self.at("00:55"),
                      exhibit_id=self.a, warning_type="temperature", message="Synthetic warning")
        self.interval("warnings", "b-warning", self.at("00:30"), self.at("00:50"),
                      exhibit_id=self.b, warning_type="brightness", message="Synthetic warning")
        self.full_coverage()

    def test_switch_splits_union_duration_original_starts_and_all_detail_types(self):
        self.switch_fixture()
        selected = self.report(self.a)
        other = self.report(self.b)
        total = self.report()
        self.assertEqual(selected["filter"], {"exhibit_id": self.a, "attribution": "unambiguous_display_intervals"})
        self.assertEqual(selected["summary"]["recorded_occupied_seconds"], 1500)
        self.assertEqual(selected["summary"]["proximity_event_count"], 2)
        self.assertEqual(selected["summary"]["scope_seconds"], 2400)
        self.assertEqual(selected["summary"]["outside_scope_seconds"], 1200)
        self.assertTrue(selected["summary"]["coverage_complete"])
        self.assertEqual(selected["data_completeness"]["proximity"]["denominator_seconds"], 2400)
        self.assertEqual(sum(row["recorded_occupied_seconds"] or 0 for row in selected["buckets"]), 1500)
        self.assertEqual(sum(row["proximity_event_count"] or 0 for row in selected["buckets"]), 2)
        self.assertEqual(selected["buckets"][-1]["status"], "confirmed_quiet")
        carry = next(row for row in selected["proximity_events"] if row["proximity_event_id"] == "starts-on-b")
        self.assertEqual(carry["clipped_duration_seconds"], 300)
        self.assertFalse(carry["event_start_attributed"])
        self.assertEqual(sum(row["event_start_attributed"] for row in selected["proximity_events"]), 2)
        self.assertEqual({row["exhibit_id"] for row in selected["display_history"]}, {self.a})
        self.assertEqual({row["warning_id"] for row in selected["environmental_warnings"]}, {"a-warning"})
        self.assertEqual(sum(row["clipped_duration_seconds"] for row in selected["environmental_warnings"]), 1800)
        self.assertEqual(selected["summary"]["warning_count"], 1)
        self.assertEqual(other["summary"]["recorded_occupied_seconds"], 1200)
        self.assertEqual(other["summary"]["proximity_event_count"], 2)
        self.assertEqual(total["summary"]["recorded_occupied_seconds"], 2700)
        self.assertEqual(total["summary"]["proximity_event_count"], 4)
        self.assertEqual(selected["data_quality"]["unattributed_proximity_event_count"], 0)

    def test_filtered_csv_heatmap_event_and_calendar_use_same_attribution(self):
        self.switch_fixture()
        report = self.report(self.a)
        response, csv_text = self.request("GET", "/api/reports/event", query={**self.query, "exhibit_id": self.a, "format": "csv"})
        self.assertEqual(response["statusCode"], 200)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        metadata = json.loads(rows[0]["detail"])
        self.assertEqual(metadata["filter"], report["filter"])
        self.assertEqual(metadata["evaluation"], report["evaluation"])
        self.assertEqual(float(rows[0]["recorded_occupied_seconds"]), 1500)
        self.assertEqual(sum(float(row["recorded_occupied_seconds"] or 0) for row in rows if row["section"] == "heatmap"), 1500)
        self.assertEqual(sum(json.loads(row["detail"])["event_start_attributed"] for row in rows if row["section"] == "proximity_events"), 2)
        _, heatmap = self.request("GET", "/api/analytics/heatmap", query={**self.query, "exhibit_id": self.a})
        self.assertEqual(heatmap["summary"], report["summary"])
        self.assertEqual(heatmap["buckets"], report["buckets"])
        _, calendar = self.request("GET", "/api/activity", query={"device_id": "analytics-device", "start_date": "2026-10-25",
            "end_date": "2026-10-25", "timezone": "UTC", "exhibit_id": self.a, "interval_minutes": "60"})
        self.assertEqual(calendar["summary"]["recorded_occupied_seconds"], 1500)
        self.assertEqual(calendar["filter"], report["filter"])
        self.assertEqual(calendar["buckets"][1]["status"], "unknown_assignment")

    def test_unknown_and_conflicting_assignment_never_certifies_zero_or_allows_double_count(self):
        self.two_exhibits()
        self.display("a", self.a, "00:00", "00:20")
        self.display("b", self.b, "00:10", "00:30")
        self.display("a-duplicate-span", self.a, "00:00", "00:10")
        self.proximity("long", "00:05", "00:50")
        self.proximity("conflicting-start", "00:12", "00:25")
        self.proximity("unknown-start", "00:35", "00:40")
        self.full_coverage()
        result = self.report(self.a, interval_minutes="10")
        self.assertEqual(result["summary"]["known_display_seconds"], 600)
        self.assertEqual(result["summary"]["ambiguous_assignment_seconds"], 600)
        self.assertEqual(result["summary"]["unknown_assignment_seconds"], 1800)
        self.assertEqual(result["summary"]["outside_scope_seconds"], 600)
        self.assertEqual(result["summary"]["scope_seconds"], 3000)
        self.assertEqual(result["summary"]["recorded_occupied_seconds"], 300)
        self.assertEqual(result["summary"]["proximity_event_count"], 1)
        self.assertEqual(result["summary"]["coverage_seconds"], 600)
        self.assertEqual(result["summary"]["missing_seconds"], 2400)
        self.assertFalse(result["summary"]["coverage_complete"])
        self.assertEqual([row["status"] for row in result["buckets"]], ["recorded_activity", "ambiguous_assignment", "no_known_display",
            "unknown_assignment", "unknown_assignment", "unknown_assignment"])
        self.assertEqual(result["data_quality"]["unattributed_proximity_event_count"], 2)
        self.assertEqual(result["data_quality"]["unattributed_recorded_occupied_seconds"], 1800)
        self.assertEqual(result["data_quality"]["contradictory_display_intervals"][0]["seconds"], 600)
        self.assertTrue(result["data_quality"]["overlapping_display_intervals"])
        self.assertIsNone(result["buckets"][2]["recorded_occupied_seconds"])
        self.assertIsNone(result["buckets"][2]["coverage_fraction"])
        self.assertIsNone(result["buckets"][2]["coverage_complete"])

    def test_no_known_display_and_confirmed_quiet_are_different(self):
        self.two_exhibits()
        self.display("known-b", self.b, "00:00", "01:00")
        self.full_coverage()
        absent = self.report(self.a)
        quiet = self.report(self.b)
        self.assertEqual(absent["summary"]["totals_interpretation"], "no_known_display")
        self.assertIsNone(absent["summary"]["recorded_occupied_seconds"])
        self.assertIsNone(absent["summary"]["coverage_complete"])
        self.assertEqual(absent["summary"]["missing_seconds"], 0)
        self.assertEqual(absent["data_completeness"]["display"]["denominator_seconds"], 0)
        self.assertTrue(all(row["status"] == "no_known_display" for row in absent["buckets"]))
        self.assertTrue(all(row["status"] == "confirmed_quiet" for row in quiet["buckets"]))
        self.assertEqual(quiet["summary"]["recorded_occupied_seconds"], 0)
        self.assertTrue(quiet["summary"]["coverage_complete"])

    def test_display_completion_required_in_addition_to_known_assignment_and_proximity_coverage(self):
        self.two_exhibits()
        self.display("known-a", self.a, "00:00", "01:00")
        self.interval("coverage", "only-proximity", self.query["start"], self.query["end"], streams=["proximity"])
        filtered = self.report(self.a)
        device_wide = self.report()
        self.assertEqual(filtered["summary"]["missing_seconds"], 3600)
        self.assertFalse(filtered["summary"]["coverage_complete"])
        self.assertIsNone(filtered["summary"]["recorded_occupied_seconds"])
        self.assertTrue(device_wide["summary"]["coverage_complete"])
        self.assertEqual(device_wide["summary"]["recorded_occupied_seconds"], 0)

    def test_today_hong_kong_partial_current_hour_future_hours_and_csv_cutoff(self):
        now = datetime(2026, 10, 26, 1, 23, 45, tzinfo=timezone.utc)  # 09:23:45 HK
        query = {"device_id": "today", "start_date": "2026-10-26", "end_date": "2026-10-26", "interval_minutes": "60"}
        with patch.object(app, "now_utc", return_value=now):
            response, today = self.request("GET", "/api/activity", query=query)
            self.assertEqual(response["statusCode"], 200, today)
            response, csv_text = self.request("GET", "/api/activity", query={**query, "format": "csv"})
        self.assertEqual(today["start"], "2026-10-25T16:00:00.000000Z")
        self.assertEqual(today["end"], "2026-10-26T16:00:00.000000Z")
        self.assertEqual(today["evaluation"]["effective_end"], "2026-10-26T01:23:45.000000Z")
        self.assertEqual(today["evaluation"]["status"], "cutoff_at_capture")
        self.assertEqual(today["summary"]["missing_seconds"], 33825)
        self.assertEqual(today["buckets"][9]["evaluated_seconds"], 1425)
        self.assertEqual(today["buckets"][9]["future_seconds"], 2175)
        self.assertEqual(today["buckets"][10]["status"], "future")
        self.assertIsNone(today["buckets"][10]["coverage_complete"])
        self.assertIsNone(today["buckets"][10]["recorded_occupied_seconds"])
        self.assertEqual(today["evaluation"]["future_seconds"], 52575)
        self.assertEqual(sum(row["evaluated_seconds"] for row in today["buckets"]), today["evaluation"]["evaluated_seconds"])
        self.assertEqual(sum(row["future_seconds"] for row in today["buckets"]), today["evaluation"]["future_seconds"])
        exported = list(csv.DictReader(io.StringIO(csv_text)))
        metadata = json.loads(exported[0]["detail"])
        self.assertEqual(metadata["evaluation"], today["evaluation"])
        self.assertEqual(metadata["date_selection"], today["date_selection"])

    def test_future_only_midnight_and_ranges_beyond_today_keep_limits_and_no_missing(self):
        now = datetime(2026, 10, 25, 16, tzinfo=timezone.utc)  # exactly HK midnight
        future = {"device_id": "future", "start_date": "2026-10-26", "end_date": "2026-10-27", "interval_minutes": "60"}
        with patch.object(app, "now_utc", return_value=now), patch.object(analytics, "read_timeline") as read:
            response, payload = self.request("GET", "/api/activity", query=future)
        self.assertEqual(response["statusCode"], 200, payload)
        read.assert_not_called()
        self.assertEqual(payload["evaluation"]["status"], "future_only")
        self.assertIsNone(payload["evaluation"]["effective_start"])
        self.assertIsNone(payload["evaluation"]["effective_end"])
        self.assertEqual(payload["summary"]["missing_seconds"], 0)
        self.assertEqual(payload["summary"]["totals_interpretation"], "future_only")
        self.assertTrue(all(row["status"] == "future" for row in payload["buckets"]))
        self.assertTrue(all(value["missing_seconds"] == 0 and value["complete"] is None for value in payload["data_completeness"].values()))
        with patch.object(app, "now_utc", return_value=now):
            response, mixed = self.request("GET", "/api/activity", query={**future, "start_date": "2026-10-25"})
            self.assertEqual(response["statusCode"], 200, mixed)
            self.assertEqual(mixed["summary"]["missing_seconds"], 86400)
            self.assertEqual(mixed["evaluation"]["future_seconds"], 172800)
            too_fine, _ = self.request("GET", "/api/activity", query={**future, "end_date": "2026-11-25", "interval_minutes": "15"})
            hourly, _ = self.request("GET", "/api/activity", query={**future, "end_date": "2026-11-25", "interval_minutes": "60"})
        self.assertEqual(too_fine["statusCode"], 400)
        self.assertEqual(hourly["statusCode"], 200)

    def test_invalid_filter_does_not_query_storage_and_ingestion_future_guard_retained(self):
        with patch.object(analytics, "read_timeline") as read:
            response, _ = self.request("GET", "/api/reports/event", query={**self.query, "exhibit_id": "../bad"})
        self.assertEqual(response["statusCode"], 400)
        read.assert_not_called()
        response, _ = self.request("POST", "/api/coverage", {**self.base, "coverage_id": "future-upload", "end": "2026-10-27T00:00:00Z", "streams": ["proximity"]})
        self.assertEqual(response["statusCode"], 400)

    def test_demo_producer_analytics_filters_before_totals_and_export_and_cap_counts_all_rows(self):
        self.proximity("demo", "00:00", "00:01")
        self.interval("proximity-events", "real", self.at("00:10"), self.at("00:20"), source="device")
        self.interval("coverage", "real-coverage", self.query["start"], self.query["end"], streams=list(analytics.STREAMS), source="device")
        context = auth.principal.set({"kind": "producer"})
        try:
            rows = analytics.read_timeline(app, "analytics-device")
            self.assertEqual(len(rows), 1)
            config = analytics.configuration(app, self.query)
            device, start, end, event, interval, zone_name, zone, _format = config
            result, details = analytics.calculate(app, rows, device, start, end, event, interval, zone_name, zone)
            self.assertEqual(result["summary"]["recorded_occupied_seconds"], 60)
            self.assertEqual(result["summary"]["proximity_event_count"], 1)
            self.assertEqual(result["summary"]["coverage_seconds"], 0)
            exported = analytics.csv_response(app, result, details)["body"]
            self.assertNotIn("real-coverage", exported)
            self.assertNotIn('""source"": ""device""', exported)
            with self.assertRaises(app.APIError) as error:
                analytics.read_timeline(app, "production-device")
            self.assertEqual(error.exception.status, 403)
            with patch.object(analytics, "MAX_SOURCE_RECORDS", 2), self.assertRaises(app.APIError) as error:
                analytics.read_timeline(app, "analytics-device")
            self.assertEqual(error.exception.status, 413)
        finally:
            auth.principal.reset(context)


class SnapshotCutoffTests(unittest.TestCase):
    setUp = media_tests.MediaReportTests.setUp
    decode = media_tests.MediaReportTests.decode

    def test_filtered_today_snapshot_freezes_cutoff_sections_profile_and_exports(self):
        now = datetime(2026, 10, 26, 1, 23, 45, tzinfo=timezone.utc)
        start, end = "2026-10-25T16:00:00Z", "2026-10-26T16:00:00Z"
        self.records.create(app, "exhibit-profile", "a", {"exhibit_id": "a", "name": "Captured"})
        def item(kind, **data):
            return {"entity_type": kind, "data": {"device_id": "today", "source": "demo", "start": start, "end": end, **data}, "received_at": start}
        rows = [item("display", display_id="display", exhibit_id="a"),
                item("coverage", coverage_id="coverage", streams=list(analytics.STREAMS)),
                item("proximity_event", proximity_event_id="event", start="2026-10-26T01:00:00Z", end="2026-10-26T01:10:00Z")]
        request = {"report_id": "today-snapshot", "device_id": "today", "start": start, "end": end,
                   "exhibit_id": "a", "interval_minutes": 60, "sections": ["history"]}
        with patch.object(app, "now_utc", return_value=now), patch.object(analytics, "read_timeline", return_value=rows):
            created = self.decode(media_reports.create_snapshot(app, request))
        self.assertEqual(created["item"]["filter"]["exhibit_id"], "a")
        self.assertEqual(created["item"]["evaluation"]["captured_at"], "2026-10-26T01:23:45.000000Z")
        captured = self.decode(media_reports.get_snapshot(app, "today-snapshot", "json"))
        self.assertEqual(captured["sections"], ["history", "missing"])
        self.assertNotIn("buckets", captured["report"])
        self.assertNotIn("proximity_events", captured["report"])
        self.assertIn("data_completeness", captured["report"])
        self.assertEqual(captured["report"]["summary"]["recorded_occupied_seconds"], 600)
        self.assertEqual(captured["profile_context"]["exhibits"][0]["profile"]["name"], "Captured")
        self.assertFalse(captured["profile_context"]["historical_settings_known"])
        self.records.replace(app, "exhibit-profile", "a", {"exhibit_id": "a", "name": "Edited later"}, 1)
        with patch.object(app, "now_utc", return_value=datetime(2026, 10, 27, tzinfo=timezone.utc)), patch.object(analytics, "read_timeline") as read:
            duplicate = self.decode(media_reports.create_snapshot(app, request))
        read.assert_not_called()
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["item"], created["item"])
        self.assertEqual(self.decode(media_reports.get_snapshot(app, "today-snapshot", "json")), captured)
        csv_text = media_reports.get_snapshot(app, "today-snapshot", "csv")["body"]
        metadata = json.loads(next(csv.DictReader(io.StringIO(csv_text)))["detail"])
        self.assertEqual(metadata["filter"], captured["report"]["filter"])
        self.assertEqual(metadata["evaluation"], captured["report"]["evaluation"])
        self.assertEqual(metadata["data_quality"]["snapshot"]["sections"], ["history", "missing"])
        with self.assertRaises(app.APIError) as error:
            media_reports.create_snapshot(app, {**request, "exhibit_id": "b"})
        self.assertEqual(error.exception.status, 409)

    def test_old_snapshot_without_new_metadata_stays_readable(self):
        with patch.object(analytics, "read_timeline", return_value=[]):
            media_reports.create_snapshot(app, {"report_id": "old", **self.query})
        item = self.records.items["report-snapshot", "old"]["data"]
        raw = json.loads(self.s3.objects[item["object_key"]]["body"])
        for key in ("filter", "evaluation"):
            item.pop(key)
            raw["report"].pop(key)
        encoded = json.dumps(raw).encode()
        import hashlib
        item["size_bytes"], item["sha256"] = len(encoded), hashlib.sha256(encoded).hexdigest()
        self.s3.objects[item["object_key"]]["body"] = encoded
        self.assertEqual(self.decode(media_reports.get_snapshot(app, "old", "json")), raw)
        self.assertIn("summary", media_reports.get_snapshot(app, "old", "csv")["body"])


if __name__ == "__main__":
    unittest.main()
