"""Real boto3 serialization check; no AWS calls or real credentials.

Managed Lambda includes boto3. Bare local Python may skip this test; run the
suite in a boto3 environment to verify the resource client's actual wire body.
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import analytics
import app
import media_reports
from test_media_reports import S3

try:
    import boto3
except ImportError:
    boto3 = None


@unittest.skipUnless(boto3, "boto3 is supplied by Lambda; use the local boto3 environment for SDK wire validation")
class SDKSerializationTests(unittest.TestCase):
    def test_snapshot_cutoff_metadata_serializes_through_real_resource_client(self):
        table = boto3.resource("dynamodb", region_name="us-east-1", aws_access_key_id="fake-unit-key",
                               aws_secret_access_key="fake-unit-secret").Table("test-readings")
        captured, assets = [], S3()
        class Intercepted(Exception):
            pass
        def capture(params, **_kwargs):
            captured.append(json.loads(params["body"]))
            raise Intercepted("HTTP request intercepted before network")
        table.meta.client.meta.events.register_first("before-call.dynamodb.TransactWriteItems", capture)
        now = datetime(2026, 10, 26, 1, 23, 45, 123456, tzinfo=timezone.utc)
        body = {"report_id": "wire-snapshot", "device_id": "v04-wire", "start": "2026-10-26T00:00:00Z",
                "end": "2026-10-26T03:00:00Z", "interval_minutes": 60, "sections": ["activity"]}
        with patch.object(app, "_table", table), patch.object(app, "now_utc", return_value=now), \
                patch.object(media_reports, "_s3", assets), patch.dict(__import__("os").environ, {"ASSET_BUCKET": "private-unit-bucket"}), \
                patch.object(media_reports.store, "get", return_value=None), patch.object(analytics, "read_timeline", return_value=[]):
            with self.assertRaises(Intercepted):
                media_reports.create_snapshot(app, body)
        self.assertEqual(len(captured), 1)
        row = next(operation["Put"]["Item"] for operation in captured[0]["TransactItems"]
                   if operation["Put"]["Item"]["PK"] == {"S": "WF#report-snapshot"})
        evaluation = row["data"]["M"]["evaluation"]["M"]
        self.assertEqual(evaluation["evaluated_seconds"], {"N": "5025.123456"})
        self.assertEqual(evaluation["future_seconds"], {"N": "5774.876544"})
        payload = json.loads(next(iter(assets.objects.values()))["body"])
        self.assertEqual(payload["report"]["evaluation"]["evaluated_seconds"], 5025.123456)
        self.assertEqual(payload["report"]["evaluation"]["effective_end"], "2026-10-26T01:23:45.123456Z")

    def test_resource_client_serializes_native_transaction_values_once(self):
        table = boto3.resource("dynamodb", region_name="us-east-1", aws_access_key_id="fake-unit-key",
                               aws_secret_access_key="fake-unit-secret").Table("test-readings")
        captured = []

        class Intercepted(Exception):
            pass

        def capture(params, **_kwargs):
            captured.append(json.loads(params["body"]))
            raise Intercepted("HTTP request intercepted before network")

        table.meta.client.meta.events.register_first("before-call.dynamodb.TransactWriteItems", capture)
        data = {"proximity_event_id": "wire-1", "device_id": "wire-device", "start": "2026-10-25T00:00:00.000000Z",
                "end": "2026-10-25T00:01:00.000000Z", "source": "demo"}
        with patch.object(app, "_table", table), patch.object(app, "now_utc", return_value=datetime(2026, 10, 26, tzinfo=timezone.utc)):
            with self.assertRaises(Intercepted):
                analytics.create_record(app, "proximity_event", data, "proximity-events")
        self.assertEqual(len(captured), 1)
        puts = [row["Put"] for row in captured[0]["TransactItems"]]
        self.assertEqual(len(puts), 2)
        self.assertEqual(puts[0]["Item"]["PK"], {"S": "PROXIMITY_EVENT#wire-1"})
        self.assertEqual(puts[1]["Item"]["PK"], {"S": "DEVICE#wire-device"})
        self.assertEqual(puts[0]["Item"]["data"]["M"]["source"], {"S": "demo"})
        self.assertTrue(all(row["ConditionExpression"] == "attribute_not_exists(PK)" for row in puts))
        self.assertTrue(all(row["TableName"] == "test-readings" for row in puts))


if __name__ == "__main__":
    unittest.main()
