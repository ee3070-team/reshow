"""Run the packaged tools against the real API handler and local test storage."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

if not os.environ.get("RESHOW_TEST_SOURCE"):
    raise unittest.SkipTest("Set RESHOW_TEST_SOURCE to the owning ReShow backend source for integration checks.")
SOURCE = Path(os.environ["RESHOW_TEST_SOURCE"])
sys.path[:0] = [str(SOURCE / "tests"), str(SOURCE / "backend")]
import app
import media_reports
import test_auth as auth_tests
from test_media_reports import S3
import demo_common
import demo_fixture
import seed_demo
import report_demo
import update_demo


class DemoTests(unittest.TestCase):
    setUp = auth_tests.AuthenticationTests.setUp
    seed = auth_tests.AuthenticationTests.seed
    event = auth_tests.AuthenticationTests.event

    def clients(self):
        owner, storage = self, S3()
        self.seed("ee3070-staff", "Administrator")
        at = datetime.now(timezone.utc)
        self.enterContext(patch.object(app, "now_utc", return_value=at))
        self.enterContext(patch.object(media_reports, "_s3", storage))
        self.enterContext(patch.dict(os.environ, {"ASSET_BUCKET": "private-test-bucket"}))
        class LocalClient:
            def __init__(self, human):
                self.human, self.calls = human, []
            def request(self, path, method="GET", payload=None, expected=(200,), headers=None):
                parsed = urlsplit(path)
                claims = {"exp": str(int(at.timestamp()) + 900), "iat": str(int(at.timestamp()) - 10)}
                event = owner.event(method, parsed.path, payload, user="ee3070-staff", claims=claims, query=dict(parse_qsl(parsed.query)))
                if not self.human:
                    event["headers"]["authorization"] = "Bearer " + auth_tests.TOKEN
                    event["requestContext"].pop("authorizer")
                event["headers"].update({key.lower(): value for key, value in (headers or {}).items()})
                response = app.lambda_handler(event, None)
                raw = response["body"]
                value = json.loads(raw) if "application/json" in response["headers"].get("content-type", "") else raw
                self.calls.append({"method": method, "path": parsed.path, "http_status": response["statusCode"]})
                if response["statusCode"] not in expected:
                    raise AssertionError((method, path, response["statusCode"], value))
                return response["statusCode"], value
            def upload_png(self, form):
                storage.objects[form["fields"]["key"]] = {"body": demo_common.PNG, "type": "image/png"}
            def download_png(self, download):
                return next(value["body"] for key, value in storage.objects.items() if key.startswith("media/final/"))
        return LocalClient(True), LocalClient(False)

    def test_full_seed_real_handler_verify_and_read_only_repeat(self):
        admin, producer = self.clients()
        manifest = demo_fixture.build()
        before = copy.deepcopy(self.table.items)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "journal.json"
            first = seed_demo.seed(admin, producer, manifest, journal)
            self.assertGreater(first["created_or_completed_count"], 25)
            self.assertTrue(all(self.table.items[key] == value for key, value in before.items()))
            result = report_demo.verify(admin, manifest)
            self.assertEqual(result["summary"]["recorded_occupied_seconds"], 600)
            calls_before = len(admin.calls), len(producer.calls)
            snapshot = copy.deepcopy(self.table.items)
            second = seed_demo.seed(admin, producer, manifest, journal)
            self.assertEqual(second["created_or_completed_count"], 0)
            self.assertEqual(self.table.items, snapshot)
            self.assertTrue(all(row["method"] == "GET" for row in admin.calls[calls_before[0]:] + producer.calls[calls_before[1]:]))

    def test_collision_preflight_writes_nothing(self):
        admin, producer = self.clients()
        manifest = demo_fixture.build()
        payload = {**manifest["records"][0]["payload"], "image_media_id": None, "name": "Conflicting existing record"}
        admin.request("/staff-api/exhibit-profiles", "POST", payload, expected=(201,))
        before = copy.deepcopy(self.table.items)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Existing record differs"):
                seed_demo.seed(admin, producer, manifest, Path(directory) / "journal.json")
        self.assertEqual(self.table.items, before)

    def test_updater_new_observations_retry_and_version_guards(self):
        admin, producer = self.clients()
        manifest = demo_fixture.build()
        with tempfile.TemporaryDirectory() as directory:
            seed_demo.seed(admin, producer, manifest, Path(directory) / "journal.json")
        args = type("Args", (), {"action": "telemetry", "update_id": "test01", "exhibit_id": "demo-test-clock",
                                  "temperature": 24, "brightness_raw": 1500, "occupied": True, "distance_cm": 60})()
        plan = update_demo.prepare(args)
        result = update_demo.apply(plan, producer=producer)
        self.assertEqual([row["http_status"] for row in result["results"]], [201, 201])
        before = copy.deepcopy(self.table.items)
        duplicate = update_demo.apply(plan, producer=producer)
        self.assertEqual([row["status"] for row in duplicate["results"]], ["duplicate", "duplicate"])
        self.assertEqual(self.table.items, before)
        edited = {"kind": "profile", "data_kind": "synthetic_demo", "hardware_connected": False,
                  "exhibit_id": "demo-test-clock", "expected_version": 1, "payload": {"warning_seconds": 45}}
        result = update_demo.apply(edited, admin=admin)
        self.assertEqual(result["version"], 2)
        case = seed_demo.exact(admin, "showcases", "demo-test-case")["item"]
        self.assertEqual(case["confirmed_profile_version"], 1)
        with self.assertRaisesRegex(ValueError, "Version changed"):
            update_demo.apply(edited, admin=admin)
        profile = {**manifest["records"][0]["payload"], "exhibit_id": "demo-test-label-extra", "tag_id": "EX-DEMO-TEST-EXTRA"}
        admin.request("/staff-api/exhibit-profiles", "POST", profile, expected=(201,))
        label_plan = {"kind": "label", "data_kind": "synthetic_demo", "hardware_connected": False,
                      "exhibit_id": "demo-test-label-extra", "expected_version": 0,
                      "payload": {"title": "Demo extra", "collection": "Testing", "description": "Synthetic label", "image_media_id": None}}
        label_result = update_demo.apply(label_plan, admin=admin)
        self.assertEqual(label_result["version"], 1)

    def test_updater_scope_and_conflicting_second_record_prevent_first_write(self):
        admin, producer = self.clients()
        bad = {"kind": "profile", "data_kind": "synthetic_demo", "hardware_connected": False,
               "exhibit_id": "real-exhibit", "expected_version": 1, "payload": {"name": "oops"}}
        with self.assertRaisesRegex(ValueError, "demo-test"):
            update_demo.apply(bad, admin=admin)
        with self.assertRaisesRegex(ValueError, "Manifest payloads differ"):
            fixture = demo_fixture.build()
            fixture["records"][0]["payload"]["name"] = "different"
            seed_demo.validate_manifest(fixture)

    def test_bad_capture_and_invalid_update_fail_before_any_write(self):
        admin, producer = self.clients()
        for instant in ("bad", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="microseconds").replace("+00:00", "Z")):
            fixture = demo_fixture.build()
            fixture["prepared_at"] = instant
            fixture["status"]["recorded_at"] = instant
            for row in fixture["records"]:
                if row["collection"] in ("tag-scans", "access-attempts"):
                    row["payload"]["recorded_at"] = instant
            with self.assertRaises(ValueError):
                seed_demo.preflight(admin, fixture)
        args = type("Args", (), {"action": "telemetry", "update_id": "invalid", "exhibit_id": "demo-test-clock",
                                  "temperature": 24, "brightness_raw": 5000, "occupied": True, "distance_cm": 60})()
        with self.assertRaisesRegex(ValueError, "range"):
            update_demo.apply(update_demo.prepare(args), producer=producer)
        self.assertFalse(any(row["method"] != "GET" for row in admin.calls + producer.calls))

    def test_update_second_record_conflict_prevents_first_upload(self):
        admin, producer = self.clients()
        manifest = demo_fixture.build()
        with tempfile.TemporaryDirectory() as directory:
            seed_demo.seed(admin, producer, manifest, Path(directory) / "journal.json")
        args = type("Args", (), {"action": "telemetry", "update_id": "conflict", "exhibit_id": "demo-test-clock",
                                  "temperature": 24, "brightness_raw": 1500, "occupied": True, "distance_cm": 60})()
        plan = update_demo.prepare(args)
        wrong = {**plan["operations"][1]["payload"], "temperature_c": 25}
        producer.request("/api/device-status", "POST", wrong, expected=(201,))
        before = copy.deepcopy(self.table.items)
        with self.assertRaisesRegex(ValueError, "different data"):
            update_demo.apply(plan, producer=producer)
        self.assertEqual(self.table.items, before)

    def test_modified_second_update_is_rejected_before_requests(self):
        _, producer = self.clients()
        args = type("Args", (), {"action": "telemetry", "update_id": "changed", "exhibit_id": "demo-test-clock",
                                  "temperature": 24, "brightness_raw": 1500, "occupied": True, "distance_cm": 60})()
        plan = update_demo.prepare(args)
        plan["operations"][1]["payload"]["lighting_mode"] = "On"
        with self.assertRaisesRegex(ValueError, "same synthetic observation"):
            update_demo.apply(plan, producer=producer)
        self.assertEqual(producer.calls, [])


if __name__ == "__main__":
    unittest.main()
