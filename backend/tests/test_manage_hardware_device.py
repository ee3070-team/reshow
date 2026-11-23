"""Exercise hardware enrollment against the existing in-memory workflow table."""

import copy
import importlib.util
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_workflows import WorkflowTable


script = Path(__file__).resolve().parents[1] / "scripts" / "manage_hardware_device.py"
spec = importlib.util.spec_from_file_location("hardware_enrollment", script)
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)

DEVICE = "reshow-test-hardware"
SHOWCASE = "test-hardware-showcase"


class Session:
    def __init__(self):
        self.table = WorkflowTable()
        self.account = manage.ACCOUNT
        self.api = manage.API

    def client(self, service):
        if service == "sts":
            return SimpleNamespace(get_caller_identity=lambda: {"Account": self.account})
        if service != "cloudformation":
            raise AssertionError("Unexpected AWS service")
        return SimpleNamespace(describe_stacks=self.describe_stacks)

    def describe_stacks(self, *, StackName):
        assert StackName == manage.STACK
        return {"Stacks": [{
            "StackName": manage.STACK,
            "StackId": f"arn:aws:cloudformation:{manage.REGION}:{manage.ACCOUNT}:stack/{manage.STACK}/test",
            "Outputs": [{"OutputKey": "ApiBaseUrl", "OutputValue": self.api},
                        {"OutputKey": "TableName", "OutputValue": manage.TABLE}],
        }]}

    def resource(self, service):
        assert service == "dynamodb"
        return SimpleNamespace(Table=self.lookup_table)

    def lookup_table(self, table):
        assert table == manage.TABLE
        return self.table


class ManageHardwareTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "hardware.json"
        self.session = Session()
        self.addCleanup(setattr, manage.app, "_table", manage.app._table)

    def run_management(self, action="enroll", **override):
        args = {"action": action, "device_id": DEVICE, "showcase_id": SHOWCASE,
                "showcase_name": "Test hardware", "credential_file": self.path, **override}
        return manage.manage(SimpleNamespace(**args), self.session)

    def test_enrolls_once_preserves_binding_and_keeps_token_out_of_db_and_report(self):
        first = self.run_management()
        token = json.loads(self.path.read_bytes())["bearer_token"]
        digest = manage.auth.hardware_token_digest(token)
        snapshot = copy.deepcopy(self.session.table.items)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(list(self.directory.iterdir()), [self.path])
        self.assertEqual(first["showcase_id"], SHOWCASE)
        self.assertEqual(first["post_paths"], ["/api/device-status", "/api/readings"])
        self.assertNotIn(token, json.dumps(first))
        self.assertNotIn(digest, json.dumps(first))
        self.assertNotIn(token, str(snapshot))
        self.assertEqual(snapshot[("WF#hardware-credential", digest)]["data"],
                         {"device_id": DEVICE, "active": True})
        self.assertEqual(snapshot[("WF#unique", f"device#{DEVICE}")]["data"]["owner"], SHOWCASE)
        self.assertEqual(self.run_management(), first)
        self.assertEqual(self.session.table.items, snapshot)

    def test_wrong_account_api_or_demo_namespace_fail_before_writes(self):
        self.session.account = "incorrect"
        with self.assertRaisesRegex(manage.EnrollmentError, "Unexpected AWS account"):
            self.run_management()
        self.session.account = manage.ACCOUNT
        self.session.api = "https://example.invalid"
        with self.assertRaisesRegex(manage.EnrollmentError, "Unexpected ReShow stack"):
            self.run_management()
        with self.assertRaisesRegex(manage.EnrollmentError, "reserved"):
            self.run_management(device_id="demo-hardware-test")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.session.table.calls, [])

    def test_cannot_enroll_second_credential_or_reassign_device(self):
        self.run_management()
        snapshot = copy.deepcopy(self.session.table.items)
        second_path = self.directory / "different.json"
        with self.assertRaisesRegex(manage.EnrollmentError, "existing private file"):
            self.run_management(credential_file=second_path)
        with self.assertRaisesRegex(manage.EnrollmentError, "conflicting showcase"):
            self.run_management(showcase_id="other-hardware")
        self.assertFalse(second_path.exists())
        self.assertEqual(self.session.table.items, snapshot)

    def test_revoke_is_idempotent_old_token_cannot_reactivate_and_new_file_can_enroll(self):
        self.run_management()
        token = json.loads(self.path.read_bytes())["bearer_token"]
        digest = manage.auth.hardware_token_digest(token)
        self.assertEqual(self.run_management("revoke")["status"], "revoked")
        self.assertEqual(self.run_management("revoke")["status"], "revoked")
        with self.assertRaisesRegex(manage.EnrollmentError, "revoked or conflicting"):
            self.run_management()
        second_path = self.directory / "next.json"
        self.assertEqual(self.run_management(credential_file=second_path)["status"], "active")
        self.assertEqual(self.run_management("revoke")["status"], "revoked")
        second_digest = manage.auth.hardware_token_digest(json.loads(second_path.read_bytes())["bearer_token"])
        self.assertFalse(self.session.table.items[("WF#hardware-credential", digest)]["data"]["active"])
        self.assertTrue(self.session.table.items[("WF#hardware-credential", second_digest)]["data"]["active"])

    def test_can_revoke_by_device_when_the_private_file_is_lost(self):
        self.run_management()
        digest = manage.auth.hardware_token_digest(json.loads(self.path.read_bytes())["bearer_token"])
        self.path.unlink()
        self.assertEqual(self.run_management("revoke", credential_file=None)["status"], "revoked")
        self.assertEqual(self.run_management("revoke", credential_file=None)["status"], "revoked")
        self.assertFalse(self.session.table.items[("WF#hardware-credential", digest)]["data"]["active"])

    def test_retry_uses_same_token_after_uncertain_transaction(self):
        transact = self.session.table.transact_write_items

        def write_then_timeout(**kwargs):
            result = transact(**kwargs)
            if any(action["Put"]["Item"]["PK"] == "WF#hardware-credential" for action in kwargs["TransactItems"]):
                raise TimeoutError("test timeout after commit")
            return result

        with patch.object(self.session.table, "transact_write_items", side_effect=write_then_timeout):
            with self.assertRaises(TimeoutError):
                self.run_management()
        first_bytes = self.path.read_bytes()
        self.assertEqual(self.run_management()["status"], "active")
        self.assertEqual(self.path.read_bytes(), first_bytes)

    def test_rejects_insecure_file_and_directory_before_data_writes(self):
        self.directory.chmod(0o755)
        with self.assertRaisesRegex(manage.EnrollmentError, "mode 0700"):
            self.run_management()
        self.directory.chmod(0o700)
        self.path.write_text("test fixture")
        self.path.chmod(0o644)
        with self.assertRaisesRegex(manage.EnrollmentError, "mode 0600"):
            self.run_management()
        self.assertEqual(self.session.table.items, {})


if __name__ == "__main__":
    unittest.main()
