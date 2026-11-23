"""Actual v0.4 analytics and stored report shapes checked against OpenAPI."""
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import analytics
import app
import media_reports
from test_media_reports import S3
from test_workflows import WorkflowTable
from test_workflow_contract import Draft4Validator, FormatChecker, RefResolver, json_schema

NOW = datetime(2026, 10, 26, 1, 23, 45, tzinfo=timezone.utc)


@unittest.skipUnless(Draft4Validator, "Use the existing contract virtualenv with jsonschema installed.")
class AnalyticsV04ContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / "backend/openapi.json").read_text())
        self.schema = json_schema(self.spec)
        self.table, self.s3 = WorkflowTable(), S3()
        for context in [patch.object(app, "_table", self.table), patch.object(app, "now_utc", return_value=NOW),
                        patch.object(media_reports, "_s3", self.s3), patch.dict(os.environ, {"ASSET_BUCKET": "private-test-bucket"})]:
            context.start()
            self.addCleanup(context.stop)
        self.left = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.right = self.left + timedelta(hours=3)

    def validate(self, value, name):
        Draft4Validator({"$ref": "#/components/schemas/" + name},
                        resolver=RefResolver.from_schema(self.schema), format_checker=FormatChecker()).validate(value)

    def row(self, kind, left=0, right=60, **data):
        return {"entity_type": kind, "data": {"device_id": "v04-contract", "source": "demo", "start": app.timestamp(self.left + timedelta(minutes=left)),
            "end": app.timestamp(self.left + timedelta(minutes=right)), **data}, "received_at": app.timestamp(self.left)}

    def calculate(self, rows, exhibit="a", left=None, right=None):
        return analytics.calculate(app, rows, "v04-contract", left or self.left, right or self.right, None,
            timedelta(minutes=10), "Asia/Hong_Kong", ZoneInfo("Asia/Hong_Kong"), exhibit_id=exhibit, captured_at=NOW)

    def test_real_filtered_unknown_conflicting_and_future_report_shapes(self):
        rows = [self.row("display", 0, 30, display_id="a", exhibit_id="a"),
                self.row("display", 20, 60, display_id="b", exhibit_id="b"),
                self.row("coverage", 0, 80, coverage_id="all", streams=list(analytics.STREAMS)),
                self.row("proximity_event", 10, 50, proximity_event_id="cross")]
        base, details = self.calculate(rows)
        self.validate(base, "Heatmap")
        self.validate({**base, **details}, "EventReport")
        self.assertIn("filter", base)
        self.assertIn("evaluation", base)
        states = {bucket["status"] for bucket in base["buckets"]}
        self.assertTrue({"future", "unknown_assignment", "ambiguous_assignment", "no_known_display"} <= states)
        self.assertEqual(base["evaluation"]["effective_end"], app.timestamp(NOW))
        self.assertIn("denominator_seconds", base["data_completeness"]["proximity"])
        for bucket in base["buckets"]:
            self.assertIn("evaluated_end", bucket)
            self.assertIn("scope_seconds", bucket)
        future, future_details = self.calculate([], left=self.right, right=self.right + timedelta(hours=1))
        self.validate({**future, **future_details}, "EventReport")
        self.assertEqual(future["summary"]["totals_interpretation"], "future_only")
        for selected in ("a", "b", None):
            base, details = self.calculate([self.row("display", 0, 60, display_id="known-b", exhibit_id="b"),
                self.row("coverage", 0, 60, coverage_id="all", streams=list(analytics.STREAMS))], exhibit=selected,
                right=self.left + timedelta(hours=1))
            self.validate({**base, **details}, "EventReport")

    def test_snapshot_actual_response_list_filter_and_section_omission_shapes(self):
        body = {"report_id": "schema-report", "device_id": "v04-contract", "start": app.timestamp(self.left),
                "end": app.timestamp(self.right), "exhibit_id": "a", "sections": ["history"], "interval_minutes": 60}
        self.validate(body, "ReportSnapshotInput")
        with patch.object(analytics, "read_timeline", return_value=[]):
            response = media_reports.create_snapshot(app, body)
            media_reports.create_snapshot(app, {**body, "report_id": "schema-report-b", "exhibit_id": "b"})
        created = json.loads(response["body"])
        self.validate(created, "CreatedReportSnapshot")
        self.assertIn("filter", created["item"])
        self.assertIn("evaluation", created["item"])
        result = json.loads(media_reports.get_snapshot(app, "schema-report", "json")["body"])
        self.validate(result, "ReportSnapshot")
        self.assertIn("data_completeness", result["report"])
        self.assertIn("missing_data", result["report"])
        self.assertNotIn("buckets", result["report"])
        self.assertEqual(result["report"]["filter"]["exhibit_id"], "a")
        page = json.loads(media_reports.route(app, "GET", "/api/report-snapshots", {"exhibit_id": "a"}, {}, {})["body"])
        self.validate(page, "ReportSnapshotList")
        self.assertEqual([row["report_id"] for row in page["items"]], ["schema-report"])
        self.assertIn("filter", page["items"][0])
        self.assertNotIn("object_key", page["items"][0])
        # Legacy snapshot fields are intentionally optional, never fabricated.
        legacy = copy.deepcopy(result)
        for key in ("filter", "evaluation"):
            legacy["report"].pop(key)
        self.validate(legacy, "ReportSnapshot")

    def test_every_selector_documents_filter_and_old_payloads_still_validate(self):
        for path in ("/api/activity", "/api/analytics/heatmap", "/api/reports/event"):
            parameters = self.spec["paths"][path]["get"]["parameters"]
            self.assertEqual(sum(parameter.get("name") == "exhibit_id" for parameter in parameters), 1)
        for selector in ({"event_id": "event"}, {"device_id": "v04-contract", "start": app.timestamp(self.left), "end": app.timestamp(self.right)}):
            self.validate({"report_id": "report", **selector}, "ReportSnapshotInput")
            self.validate({"report_id": "report", **selector, "exhibit_id": "a"}, "ReportSnapshotInput")


if __name__ == "__main__":
    unittest.main()
