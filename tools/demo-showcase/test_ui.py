"""Desktop editor service behavior; no display, credentials or network required."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from demo_ui_service import DemoEditor


PROFILE = {"name": "Demo clock", "collection": "Demo", "description": "Test exhibit",
    "temperature_min_c": "18", "temperature_max_c": "28", "brightness_min_percent": "10",
    "brightness_max_percent": "80", "warning_seconds": "30"}


class LocalClient:
    def __init__(self):
        self.rows, self.calls, self.fail_once, self.before = {}, [], False, lambda: None

    def request(self, path, method="GET", payload=None, expected=(200,), headers=None):
        self.before()
        self.calls.append((path, method, copy.deepcopy(payload), headers))
        if path == "/staff-api/session":
            return 200, {"user": {"staff_id": "ee3070-staff", "role": "Administrator"}}
        if method == "GET":
            return (200, {"item": copy.deepcopy(self.rows[path])}) if path in self.rows else (404, {})
        if method == "POST":
            key = "reading_id" if path.endswith("readings") else "status_id"
            self.rows[path + "/" + payload[key]] = copy.deepcopy(payload)
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("Request interrupted; retry unchanged manifest/IDs.")
            return 201, {"status": "created", "item": copy.deepcopy(payload)}
        old = self.rows.get(path, {"version": 0})
        if headers != {"If-Match": str(old["version"])}:
            raise AssertionError("The save did not use the loaded version.")
        updated = {**old, "version": old["version"] + 1}
        if method == "PUT":
            updated["content"] = copy.deepcopy(payload)
        else:
            updated.update(payload)
        self.rows[path] = updated
        return 200, {"item": copy.deepcopy(updated)}


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def test_saved_plan_precedes_requests_and_recovers_an_uncertain_first_write(self):
        producer = LocalClient()
        editor = DemoEditor(self.folder, producer=producer)
        def saved_before_request():
            self.assertIsNotNone(editor.last_plan)
            self.assertTrue(editor.last_plan.is_file())
        producer.before, producer.fail_once = saved_before_request, True
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            editor.send_telemetry("demo-test-clock", "25.7", "1800", True, "60")
        original = editor.last_plan.read_bytes()
        self.assertEqual(len(producer.rows), 1)
        result = editor.retry_telemetry()
        self.assertEqual([row["status"] for row in result["result"]["results"]], ["duplicate", "created"])
        self.assertEqual(len(producer.rows), 2)
        self.assertEqual(editor.last_plan.read_bytes(), original)
        self.assertEqual([row["status"] for row in editor.retry_telemetry()["result"]["results"]], ["duplicate", "duplicate"])
        self.assertEqual(len(producer.rows), 2)

    def test_invalid_sensor_input_or_outside_exhibit_never_requests_or_saves(self):
        producer = LocalClient()
        editor = DemoEditor(self.folder / "plans", producer=producer)
        for args in (("demo-test-clock", "nan", "1800", True, ""),
                     ("demo-test-clock", "25", "1.5", True, ""),
                     ("demo-test-clock", "25", "1800", 1, ""),
                     ("demo-test-clock", "25", "1800", True, "-1"),
                     ("other-exhibit", "25", "1800", True, "")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                editor.send_telemetry(*args)
        self.assertEqual(producer.calls, [])
        self.assertFalse(editor.plans_dir.exists())

    def test_changed_saved_plan_is_not_retried(self):
        producer = LocalClient()
        editor = DemoEditor(self.folder, producer=producer)
        editor.send_telemetry("demo-test-clock", "25", "1800", False, "")
        prior_calls = len(producer.calls)
        editor.last_plan.write_text(editor.last_plan.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "saved plan changed"):
            editor.retry_telemetry()
        self.assertEqual(len(producer.calls), prior_calls)

    def test_open_saved_plan_after_restart_reuses_the_same_observation_ids(self):
        producer = LocalClient()
        first = DemoEditor(self.folder, producer=producer)
        first.send_telemetry("demo-test-clock", "25", "1800", False, "")
        before = copy.deepcopy(producer.rows)
        restarted = DemoEditor(self.folder, producer=producer)
        restarted.select_retry(first.last_plan)
        restarted.retry_telemetry()
        self.assertEqual(producer.rows, before)

    def test_stale_profile_version_does_not_overwrite_current_data(self):
        admin = LocalClient()
        path = "/staff-api/exhibit-profiles/demo-test-clock"
        admin.rows[path] = {"exhibit_id": "demo-test-clock", "version": 2, "name": "Newer saved name"}
        before = copy.deepcopy(admin.rows)
        editor = DemoEditor(self.folder, admin=admin)
        with self.assertRaisesRegex(ValueError, "Version changed"):
            editor.save_edit("profile", "demo-test-clock", 1, PROFILE)
        self.assertEqual(admin.rows, before)
        self.assertTrue(all(call[1] == "GET" for call in admin.calls))

    def test_versioned_profile_and_label_saves_keep_confirmed_device_state_separate(self):
        admin = LocalClient()
        profile = "/staff-api/exhibit-profiles/demo-test-clock"
        draft = "/staff-api/label-drafts/demo-test-clock"
        admin.rows[profile] = {"exhibit_id": "demo-test-clock", "version": 1}
        editor = DemoEditor(self.folder, admin=admin)
        result = editor.save_edit("profile", "demo-test-clock", 1, PROFILE)
        self.assertEqual(result["result"]["version"], 2)
        self.assertFalse(result["result"]["device_reapplied"])
        result = editor.save_edit("label", "demo-test-clock", 0,
            {"title": "Draft", "collection": "Demo", "description": "Saved draft", "image_media_id": ""})
        self.assertEqual(result["result"]["version"], 1)
        self.assertEqual(admin.rows[draft]["content"]["title"], "Draft")
        self.assertFalse(any("/commands" in call[0] for call in admin.calls))
        self.assertTrue(all("token" not in path.read_text().lower() for path in self.folder.glob("*.json")))

    def test_invalid_profile_range_or_label_media_never_requests(self):
        admin = LocalClient()
        editor = DemoEditor(self.folder, admin=admin)
        with self.assertRaisesRegex(ValueError, "minimum"):
            editor.save_edit("profile", "demo-test-clock", 1, {**PROFILE, "temperature_min_c": "30"})
        with self.assertRaises(ValueError):
            editor.save_edit("label", "demo-test-clock", 1,
                {"title": "Draft", "collection": "Demo", "description": "Saved draft", "image_media_id": "other-image"})
        self.assertEqual(admin.calls, [])

    def test_load_rejects_an_unexpected_device_binding(self):
        admin = LocalClient()
        admin.rows["/staff-api/showcases/demo-test-case"] = {"device_id": "other-device"}
        editor = DemoEditor(self.folder, admin=admin)
        with self.assertRaisesRegex(ValueError, "device binding"):
            editor.load("demo-test-clock")
        self.assertEqual(len(admin.calls), 2)

    def test_replacing_the_token_file_is_used_by_the_next_action(self):
        path = self.folder / "private.json"
        path.write_text(json.dumps({"tokens": {"access_token": "first-test-token"}}))
        os.chmod(path, 0o600)
        editor = DemoEditor(self.folder, admin_tokens=path)
        with patch("demo_ui_service.Client") as client:
            editor.admin()
            path.write_text(json.dumps({"tokens": {"access_token": "replacement-test-token"}}))
            editor.admin()
        self.assertEqual([call.args[0] for call in client.call_args_list], ["first-test-token", "replacement-test-token"])

    def test_malformed_private_file_is_a_recoverable_validation_error(self):
        path = self.folder / "private.json"
        path.write_text("[]")
        os.chmod(path, 0o600)
        editor = DemoEditor(self.folder, producer_access=path, admin_tokens=path)
        with patch("demo_ui_service.Client") as client:
            with self.assertRaises(ValueError):
                editor.producer()
            with self.assertRaises(ValueError):
                editor.admin()
            client.assert_not_called()

    def test_loaded_values_include_current_versions_and_no_credential_data(self):
        admin = LocalClient()
        admin.rows["/staff-api/showcases/demo-test-case"] = {"device_id": "demo-test-device", "device_status": {"temperature_c": 24.2}}
        admin.rows["/staff-api/exhibit-profiles/demo-test-clock"] = {"exhibit_id": "demo-test-clock", "version": 2, "name": "Current clock"}
        admin.rows["/staff-api/label-drafts/demo-test-clock"] = {"exhibit_id": "demo-test-clock", "version": 3, "content": {"title": "Current draft"}}
        editor = DemoEditor(self.folder, admin=admin)
        result = editor.load("demo-test-clock")
        self.assertEqual(result["profile"]["version"], 2)
        self.assertEqual(result["draft"]["version"], 3)
        self.assertEqual(result["showcase"]["device_status"]["temperature_c"], 24.2)
        admin.rows["/staff-api/exhibit-profiles/demo-test-clock"]["version"] = True
        with self.assertRaisesRegex(ValueError, "current version"):
            editor.load("demo-test-clock")


if __name__ == "__main__":
    unittest.main()
