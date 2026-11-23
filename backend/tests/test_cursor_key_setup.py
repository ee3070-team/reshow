"""The IAM-only cursor-key setup is opt-in, retry-safe and never rotates keys."""
import base64
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cursor_key_setup", ROOT / "scripts" / "prepare_staff_cursor_key.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class Table:
    def __init__(self):
        self.item = None
        self.puts = 0

    def get_item(self, **kwargs):
        assert kwargs == {"Key": setup.KEY, "ConsistentRead": True}
        return {"Item": copy.deepcopy(self.item)} if self.item else {}

    def put_item(self, **kwargs):
        assert kwargs["ConditionExpression"] == "attribute_not_exists(PK)"
        assert self.item is None
        self.item = copy.deepcopy(kwargs["Item"])
        self.puts += 1


class Session:
    def __init__(self, account="835822826851"):
        self.account = account
        self.table = Table()

    def client(self, service):
        if service == "sts":
            return SimpleNamespace(get_caller_identity=lambda: {"Account": self.account})
        assert service == "cloudformation"
        return SimpleNamespace(describe_stacks=lambda **kwargs: {"Stacks": [{"Outputs": [{"OutputKey": "TableName", "OutputValue": "expected"}]}]})

    def resource(self, service):
        assert service == "dynamodb"
        def table(name):
            assert name == "expected"
            return self.table
        return SimpleNamespace(Table=table)


class CursorKeySetupTests(unittest.TestCase):
    def test_preparation_is_read_only_and_provisioning_is_retry_safe_without_returning_key(self):
        session = Session()
        self.assertEqual(setup.prepare(session)["status"], "prepared_only")
        self.assertEqual(session.table.puts, 0)
        created = setup.prepare(session, provision=True)
        self.assertTrue(created["created"])
        self.assertFalse(created["key_material_returned"])
        item = copy.deepcopy(session.table.item)
        self.assertEqual(len(base64.urlsafe_b64decode(item["data"]["encryption_key"])), 32)
        self.assertNotIn("encryption_key", created)
        self.assertFalse(setup.prepare(session, provision=True)["created"])
        self.assertEqual(session.table.item, item)
        self.assertEqual(session.table.puts, 1)

    def test_wrong_account_or_malformed_existing_key_is_preserved_without_write(self):
        session = Session("unexpected")
        with self.assertRaisesRegex(ValueError, "Unexpected AWS account"):
            setup.prepare(session, provision=True)
        self.assertEqual(session.table.puts, 0)
        session.account = "835822826851"
        session.table.item = {**setup.KEY, "data": {"purpose": "unrelated", "encryption_key": "bad"}}
        original = copy.deepcopy(session.table.item)
        with self.assertRaisesRegex(ValueError, "not overwritten"):
            setup.prepare(session, provision=True)
        self.assertEqual(session.table.item, original)
        self.assertEqual(session.table.puts, 0)


if __name__ == "__main__":
    unittest.main()
