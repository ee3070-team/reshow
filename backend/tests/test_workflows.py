"""Workflow transaction/data-integrity checks; synthetic engineering evidence only."""
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app
import workflows
import workflow_store as store
try:
    import boto3
except ImportError:
    boto3 = None

TOKEN = "unit-workflow-token-" + "x" * 32
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


class Conflict(Exception):
    response = {"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}


class PutConflict(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class WorkflowTable:
    name = "test-workflows"
    def __init__(self):
        self.items = {}
        self.calls = []
        self.meta = SimpleNamespace(client=self)
        self.fail_transaction = False
        self.before_transaction = None

    def get_item(self, **kwargs):
        self.calls.append(("get_item", copy.deepcopy(kwargs)))
        key = kwargs["Key"]
        item = self.items.get((key["PK"], key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, **kwargs):
        self.calls.append(("put_item", copy.deepcopy(kwargs)))
        item = kwargs["Item"]
        key = (item["PK"], item["SK"])
        if kwargs.get("ConditionExpression") != "attribute_not_exists(PK)":
            raise AssertionError("Immutable PutItem required")
        if key in self.items:
            raise PutConflict()
        self.items[key] = copy.deepcopy(item)
        return {}

    def transact_write_items(self, **kwargs):
        self.calls.append(("transact_write_items", copy.deepcopy(kwargs)))
        actions = kwargs["TransactItems"]
        keys = [(a["Put"]["Item"]["PK"], a["Put"]["Item"]["SK"]) for a in actions]
        self.assert_unique(keys)
        if self.before_transaction:
            hook, self.before_transaction = self.before_transaction, None
            hook(self.items)
        for action, key in zip(actions, keys):
            put = action["Put"]
            if put["TableName"] != self.name:
                raise AssertionError("Wrong table")
            condition = put["ConditionExpression"]
            if condition == "attribute_not_exists(PK)":
                if key in self.items:
                    raise Conflict()
            elif condition == "#version = :version":
                if key not in self.items or self.items[key]["version"] != put["ExpressionAttributeValues"][":version"]:
                    raise Conflict()
            else:
                raise AssertionError("Every put requires an explicit immutable/create or version condition")
        if self.fail_transaction:
            raise Conflict()
        self.items.update({key: copy.deepcopy(action["Put"]["Item"]) for action, key in zip(actions, keys)})
        return {}

    @staticmethod
    def assert_unique(keys):
        if len(keys) != len(set(keys)):
            raise AssertionError("A transaction cannot write the same item twice")

    def query(self, **kwargs):
        if kwargs.get("IndexName") == "device-readings-index":
            from test_api import FakeTable
            return FakeTable.query(self, **kwargs)
        self.calls.append(("query", copy.deepcopy(kwargs)))
        if kwargs.get("ConsistentRead") is not True or "IndexName" in kwargs:
            raise AssertionError("Expected strong base-table bounded Query")
        values = kwargs["ExpressionAttributeValues"]
        if ":device" in values:
            items = sorted((copy.deepcopy(item) for item in self.items.values() if item["PK"] == values[":device"]), key=lambda item: item["SK"])
            if kwargs.get("ExclusiveStartKey"):
                items = [item for item in items if item["SK"] > kwargs["ExclusiveStartKey"]["SK"]]
            limit = kwargs["Limit"]
            result = {"Items": items[:limit]}
            if len(items) > limit:
                result["LastEvaluatedKey"] = {"PK": values[":device"], "SK": items[limit - 1]["SK"]}
            return result
        partition = values[":pk"]
        items = [copy.deepcopy(item) for item in self.items.values() if item["PK"] == partition]
        if ":start" in values:
            items = [item for item in items if values[":start"] <= item["SK"] <= values[":end"]]
        items.sort(key=lambda item: item["SK"], reverse=not kwargs["ScanIndexForward"])
        if kwargs.get("ExclusiveStartKey"):
            key = kwargs["ExclusiveStartKey"]["SK"]
            items = [item for item in items if item["SK"] < key]
        limit = kwargs["Limit"]
        result = {"Items": items[:limit]}
        if len(items) > limit:
            result["LastEvaluatedKey"] = {"PK": partition, "SK": items[limit - 1]["SK"]}
        return result


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.table = WorkflowTable()
        self.env = patch.dict(os.environ, {"API_TOKEN": TOKEN, "AUTH_ENFORCED": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.storage = patch.object(app, "_table", self.table)
        self.storage.start()
        self.addCleanup(self.storage.stop)
        self.clock = patch.object(app, "now_utc", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile = {"exhibit_id": "ex-1", "name": "First", "description": "Artifact", "tag_id": "EX-001",
                        "temperature_min_c": 18, "temperature_max_c": 28, "brightness_min_percent": 10,
                        "brightness_max_percent": 80, "warning_seconds": 30}
        self.person = {"staff_id": "staff-1", "name": "Person", "email": "User@Example.com", "role": "Operator"}

    def request(self, method, path, data=None, query=None, match=None):
        headers = {"authorization": "Bearer " + TOKEN, "content-type": "application/json"}
        if match is not None:
            headers["if-match"] = str(match)
        event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": headers,
                 "queryStringParameters": query, "body": json.dumps(data) if data is not None else ""}
        result = app.lambda_handler(event, None)
        return result["statusCode"], json.loads(result["body"])

    def post(self, path, data, expected=201):
        status, body = self.request("POST", "/api/" + path, data)
        self.assertEqual(status, expected, body)
        return body

    def create_case(self, suffix="1"):
        return self.post("showcases", {"showcase_id": "case-" + suffix, "device_id": "device-" + suffix, "name": "Case " + suffix})

    def create_profile(self, data=None):
        return self.post("exhibit-profiles", data or self.profile)

    def command(self, command_id, kind, payload, case="case-1", expected=201):
        return self.post("commands", {"command_id": command_id, "showcase_id": case, "type": kind, "payload": payload}, expected)

    def ack(self, command_id, success=True, receipt_id=None, device="device-1", at=None, expected=201):
        data = {"acknowledgement_id": receipt_id or "ack-" + command_id, "device_id": device,
                "recorded_at": at or app.timestamp(NOW + timedelta(seconds=1)), "source": "demo", "success": success}
        return self.post("commands/" + command_id + "/acknowledgements", data, expected)

    def observe(self, status_id="status-current", device="device-1", at=None):
        data = {key: None for key in workflows.STATUS_FIELDS}
        data.update(status_id=status_id, device_id=device, recorded_at=at or app.timestamp(app.now_utc()), source="demo")
        return self.post("device-status", data)

    def recover(self, command_id, recovery_id="recovery-1", expected=201):
        return self.post("commands/" + command_id + "/recovery-requests", {"recovery_id": recovery_id, "reason": "Synthetic ambiguous HTTP timeout."}, expected)

    def recovery_receipt(self, command_id, recovery_id="recovery-1", receipt_id="recovery-ack-1", outcome="not_applied", **changes):
        command = store.get(app, "command", command_id)["data"]
        data = {"acknowledgement_id": receipt_id, "recovery_id": recovery_id, "device_id": command["device_id"],
                "recorded_at": app.timestamp(app.now_utc()), "source": "demo", "fenced_generation": command["recovery"]["fence_generation"],
                "observed_status_id": "recovery-status", "outcome": outcome}
        data.update(changes)
        return data

    def test_catalog_create_retry_patch_preserves_legacy_and_original_create_identity(self):
        initial = self.create_profile()["item"]
        old_core = copy.deepcopy(self.table.items[("EXHIBIT#ex-1", "META")])
        status, body = self.request("PATCH", "/api/exhibit-profiles/ex-1", {"name": "Changed"}, match=1)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["item"]["version"], 2)
        self.assertEqual(body["item"]["name"], "Changed")
        self.assertEqual(self.table.items[("EXHIBIT#ex-1", "META")], old_core)
        retry = self.post("exhibit-profiles", self.profile, 200)
        self.assertEqual(retry["item"]["name"], "Changed")
        self.assertEqual(retry["item"]["version"], 2)
        self.post("exhibit-profiles", {**self.profile, "name": "Different create"}, 409)
        status, _ = self.request("PATCH", "/api/exhibit-profiles/ex-1", {"name": "Rollback"}, match=1)
        self.assertEqual(status, 409)
        status, _ = self.request("PATCH", "/api/exhibit-profiles/ex-1", {"name": "No version"})
        self.assertEqual(status, 428)
        self.assertEqual(initial["brightness_min_percent"], 10)
        self.assertEqual(old_core["data"]["brightness_min_raw"], 410)

    def test_catalog_adopts_matching_legacy_core_and_rejects_mismatch_without_writes(self):
        clean = workflows.validate_profile(app, self.profile)
        core = workflows.legacy_core(clean)
        legacy = {"PK": "EXHIBIT#ex-1", "SK": "META", "entity_type": "exhibit", "data": core, "received_at": "old"}
        self.table.items[(legacy["PK"], legacy["SK"])] = copy.deepcopy(legacy)
        self.create_profile()
        self.assertEqual(self.table.items[(legacy["PK"], legacy["SK"])], legacy)
        bad = {**self.profile, "exhibit_id": "ex-2"}
        self.table.items[("EXHIBIT#ex-2", "META")] = {**legacy, "data": {**core, "name": "Does not match"}}
        before = copy.deepcopy(self.table.items)
        self.post("exhibit-profiles", bad, 409)
        self.assertEqual(self.table.items, before)

    def test_legacy_adoption_normalizes_only_copied_tag_and_preserves_original_bytes(self):
        for index, historical_tag in enumerate(("ex-legacy-01", "Ex-LeGaCy-02")):
            with self.subTest(historical_tag=historical_tag):
                profile = {**self.profile, "exhibit_id": "legacy-" + str(index), "tag_id": historical_tag.upper()}
                core = workflows.legacy_core(workflows.validate_profile(app, profile))
                legacy = {"PK": "EXHIBIT#" + profile["exhibit_id"], "SK": "META", "entity_type": "exhibit",
                          "data": {**core, "tag_id": historical_tag}, "received_at": "2026-10-25T00:00:00.000000Z"}
                key = (legacy["PK"], legacy["SK"])
                self.table.items[key] = copy.deepcopy(legacy)
                original_bytes = json.dumps(legacy, sort_keys=True, default=app.json_default).encode()
                created = self.create_profile(profile)
                self.assertEqual(created["item"]["tag_id"], historical_tag.upper())
                self.assertEqual(json.dumps(self.table.items[key], sort_keys=True, default=app.json_default).encode(), original_bytes)
                transaction = next(params for action, params in reversed(self.table.calls) if action == "transact_write_items")
                self.assertFalse(any(action["Put"]["Item"]["PK"] == legacy["PK"] for action in transaction["TransactItems"]))
                registry = store.get(app, "unique", "tag#" + historical_tag.upper())
                self.assertEqual(registry["data"]["owner"], profile["exhibit_id"])
                after_create = copy.deepcopy(self.table.items)
                self.post("exhibit-profiles", {**profile, "tag_id": historical_tag}, 200)
                self.assertEqual(self.table.items, after_create)
                self.post("exhibit-profiles", {**profile, "exhibit_id": "duplicate-tag-" + str(index)}, 409)
                self.assertEqual(self.table.items, after_create)

    def test_legacy_adoption_still_rejects_different_tag_name_and_raw_threshold(self):
        mismatches = (("tag_id", "EX-GENUINELY-DIFFERENT"), ("name", "Different historical name"),
                      ("brightness_min_raw", 411))
        for index, (field, value) in enumerate(mismatches):
            with self.subTest(field=field):
                profile = {**self.profile, "exhibit_id": "mismatch-" + str(index), "tag_id": "EX-MISMATCH-" + str(index)}
                core = workflows.legacy_core(workflows.validate_profile(app, profile))
                legacy = {"PK": "EXHIBIT#" + profile["exhibit_id"], "SK": "META", "entity_type": "exhibit",
                          "data": {**core, "tag_id": profile["tag_id"].lower(), field: value}, "received_at": "unchanged"}
                self.table.items[(legacy["PK"], legacy["SK"])] = copy.deepcopy(legacy)
                before = copy.deepcopy(self.table.items)
                status, body = self.request("POST", "/api/exhibit-profiles", profile)
                self.assertEqual(status, 409, body)
                self.assertEqual(body["error"]["code"], "legacy_profile_conflict")
                self.assertEqual(self.table.items, before)

    def test_unique_tag_registry_move_atomicity_and_old_tag_release(self):
        self.create_profile()
        before = copy.deepcopy(self.table.items)
        self.post("exhibit-profiles", {**self.profile, "exhibit_id": "ex-2"}, 409)
        self.assertEqual(self.table.items, before)
        self.table.fail_transaction = True
        status, _ = self.request("PATCH", "/api/exhibit-profiles/ex-1", {"tag_id": "EX-002"}, match=1)
        self.assertEqual(status, 409)
        self.assertEqual(self.table.items, before)
        self.table.fail_transaction = False
        status, body = self.request("PATCH", "/api/exhibit-profiles/ex-1", {"tag_id": "EX-002"}, match=1)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("GET", "/api/exhibit-profiles/by-tag/ex-002")[1]["item"]["exhibit_id"], "ex-1")
        self.assertEqual(self.request("GET", "/api/exhibit-profiles/by-tag/EX-001")[0], 404)
        self.create_profile({**self.profile, "exhibit_id": "ex-2"})

    def test_staff_unique_normalized_email_and_card_fields_not_directly_editable(self):
        self.post("staff", self.person)
        self.post("staff", {**self.person, "staff_id": "staff-2", "email": "user@example.COM"}, 409)
        status, _ = self.request("PATCH", "/api/staff/staff-1", {"card_enabled": True}, match=1)
        self.assertEqual(status, 400)
        status, body = self.request("PATCH", "/api/staff/staff-1", {"role": "Viewer"}, match=1)
        self.assertEqual(status, 200, body)
        retry = self.post("staff", self.person, 200)
        self.assertEqual(retry["item"]["role"], "Viewer")
        self.assertFalse(retry["item"]["card_enabled"])

    def test_showcase_unknown_measurements_remain_null_not_zero_or_online(self):
        self.create_case()
        status, body = self.request("GET", "/api/showcases/case-1")
        self.assertEqual(status, 200)
        self.assertIsNone(body["item"]["device_status"])
        self.assertIsNone(body["item"]["freshness"]["measurement_age_seconds"])
        self.assertIsNone(body["item"]["freshness"]["contact_stale"])
        self.assertNotIn("online", body["item"])
        self.post("showcases", {"showcase_id": "other", "device_id": "device-1", "name": "Other"}, 409)

    def test_status_exact_dedup_out_of_order_measurements_and_contact_freshness(self):
        self.create_case()
        data = {key: None for key in workflows.STATUS_FIELDS}
        data.update(status_id="status-new", device_id="device-1", recorded_at="2026-10-26T10:00:00Z", source="demo", temperature_c=25, local_control=True)
        self.post("device-status", data)
        self.post("device-status", data, 200)
        self.post("device-status", {**data, "temperature_c": 20}, 409)
        self.post("device-status", {**data, "status_id": "status-old", "recorded_at": "2026-10-26T09:00:00Z", "temperature_c": 10})
        status, body = self.request("GET", "/api/showcases/case-1")
        self.assertEqual(status, 200, body)
        view = body["item"]
        self.assertEqual(view["device_status"]["temperature_c"], 25)
        self.assertTrue(view["freshness"]["measurements_stale"])
        self.assertFalse(view["freshness"]["contact_stale"])
        self.assertEqual(view["freshness"]["measurement_age_seconds"], 7200)
        self.assertEqual(view["freshness"]["contact_age_seconds"], 0)
        self.assertIsNone(view["device_status"]["occupied"])
        self.assertTrue(view["device_status"]["local_control"])
        self.post("device-status", {**data, "status_id": "invalid-local", "local_control": "active"}, 400)
        self.assertFalse(any(key[0].startswith("COVERAGE#") for key in self.table.items))

    def test_profile_percent_validation_and_ready_media_reference(self):
        self.post("exhibit-profiles", {**self.profile, "brightness_min_percent": True}, 400)
        self.post("exhibit-profiles", {**self.profile, "brightness_max_percent": 101}, 400)
        self.post("exhibit-profiles", {**self.profile, "image_media_id": "unknown"}, 404)
        store.create(app, "media", "image-1", {"media_id": "image-1", "status": "pending"})
        self.post("exhibit-profiles", {**self.profile, "image_media_id": "image-1"}, 409)
        old = store.get(app, "media", "image-1")
        store.replace(app, "media", "image-1", {**old["data"], "status": "ready"}, old["version"])
        self.create_profile({**self.profile, "image_media_id": "image-1"})

    def test_assignment_pending_failure_success_wrong_device_and_duplicate_ack(self):
        self.create_case()
        self.create_case("2")
        self.create_profile()
        command = self.command("assign-1", "assignment", {"exhibit_id": "ex-1"})
        self.assertEqual(command["item"]["status"], "pending")
        self.assertIsNone(store.get(app, "showcase", "case-1")["data"]["confirmed_exhibit_id"])
        self.command("assign-2", "assignment", {"exhibit_id": "ex-1"}, expected=409)
        self.ack("assign-1", device="device-2", expected=409)
        self.assertEqual(store.get(app, "command", "assign-1")["data"]["status"], "pending")
        failed = self.ack("assign-1", success=False)
        self.assertEqual(failed["item"]["status"], "failed")
        self.assertIsNone(store.get(app, "showcase", "case-1")["data"]["confirmed_exhibit_id"])
        self.command("assign-2", "assignment", {"exhibit_id": "ex-1"})
        self.ack("assign-2")
        before = copy.deepcopy(self.table.items)
        self.ack("assign-2", expected=200)
        self.assertEqual(self.table.items, before)
        self.ack("assign-1", receipt_id="different-old-ack", expected=409)
        self.assertEqual(store.get(app, "showcase", "case-1")["data"]["confirmed_exhibit_id"], "ex-1")
        self.assertFalse(any(key[0].startswith(("DISPLAY#", "COVERAGE#")) for key in self.table.items))

    def test_command_retry_after_ack_is_idempotent_and_changed_payload_conflicts(self):
        self.create_case()
        self.command("light-1", "lighting", {"mode": "On"})
        self.ack("light-1")
        same = self.command("light-1", "lighting", {"mode": "On"}, expected=200)
        self.assertEqual(same["item"]["status"], "acknowledged")
        self.command("light-1", "lighting", {"mode": "Off"}, expected=409)
        self.assertEqual(store.get(app, "showcase", "case-1")["data"]["lighting_mode"], "On")
        self.assertNotIn("lighting_output", store.get(app, "showcase", "case-1")["data"])

    def test_stale_ack_cannot_roll_confirmed_lighting_back(self):
        self.create_case()
        self.command("light-1", "lighting", {"mode": "On"})
        self.ack("light-1", at=app.timestamp(NOW + timedelta(seconds=10)))
        self.command("light-2", "lighting", {"mode": "Off"})
        body = self.ack("light-2", at=app.timestamp(NOW + timedelta(seconds=5)))
        self.assertEqual(body["item"]["status"], "failed")
        self.assertIn("older than confirmed", body["item"]["failure_reason"])
        self.assertEqual(store.get(app, "showcase", "case-1")["data"]["lighting_mode"], "On")

    def test_label_draft_version_snapshot_and_assignment_recheck_on_ack(self):
        self.create_case()
        self.create_profile()
        self.create_profile({**self.profile, "exhibit_id": "ex-2", "tag_id": "EX-002"})
        for media_id in ("label-image-first", "label-image-second"):
            store.create(app, "media", media_id, {"media_id": media_id, "status": "ready"})
        content = {"title": "Label A", "collection": "Test", "description": "Description",
                   "image_media_id": "label-image-first"}
        status, _ = self.request("PUT", "/api/label-drafts/ex-1", content, match=0)
        self.assertEqual(status, 201)
        self.command("label-first", "label", {"exhibit_id": "ex-1", "draft_version": 1}, expected=409)
        self.command("assign-1", "assignment", {"exhibit_id": "ex-1"})
        self.ack("assign-1")
        self.command("label-1", "label", {"exhibit_id": "ex-1", "draft_version": 1})
        status, updated = self.request("PUT", "/api/label-drafts/ex-1",
                                       {**content, "title": "Label B", "image_media_id": "label-image-second"}, match=1)
        self.assertEqual(status, 200, updated)
        self.assertNotIn("status", updated)
        self.assertIsNone(store.get(app, "showcase", "case-1")["data"]["confirmed_label"])
        self.assertEqual(store.get(app, "command", "label-1")["data"]["payload"]["content"], content)
        self.ack("label-1")
        confirmed = store.get(app, "showcase", "case-1")["data"]["confirmed_label"]
        self.assertEqual(confirmed["content"], content)
        self.command("label-2", "label", {"exhibit_id": "ex-1", "draft_version": 2})
        self.command("assign-2", "assignment", {"exhibit_id": "ex-2"})
        self.ack("assign-2", at=app.timestamp(NOW + timedelta(seconds=2)))
        body = self.ack("label-2", at=app.timestamp(NOW + timedelta(seconds=3)))
        self.assertEqual(body["item"]["status"], "failed")
        self.assertEqual(store.get(app, "showcase", "case-1")["data"]["confirmed_label"], confirmed)

    def test_card_global_pending_active_unique_failure_release_and_confirmation_only_ack(self):
        self.create_case()
        self.post("staff", self.person)
        self.post("staff", {**self.person, "staff_id": "staff-2", "email": "other@example.com"})
        payload = {"staff_id": "staff-1", "card_id": "CARD-001", "enabled": True}
        self.command("card-1", "card", payload)
        self.assertFalse(store.get(app, "staff", "staff-1")["data"]["card_enabled"])
        self.command("card-conflict", "card", {**payload, "staff_id": "staff-2"}, expected=409)
        self.ack("card-1", success=False)
        self.command("card-2", "card", {**payload, "staff_id": "staff-2"})
        self.ack("card-2")
        self.assertTrue(store.get(app, "staff", "staff-2")["data"]["card_enabled"])
        self.command("card-3", "card", payload, expected=409)
        self.command("card-disable", "card", {**payload, "staff_id": "staff-2", "enabled": False})
        self.ack("card-disable", success=False, at=app.timestamp(NOW + timedelta(seconds=2)))
        self.assertTrue(store.get(app, "staff", "staff-2")["data"]["card_enabled"])
        self.command("card-still-owned", "card", payload, expected=409)
        self.command("card-disable-2", "card", {**payload, "staff_id": "staff-2", "enabled": False})
        self.ack("card-disable-2", at=app.timestamp(NOW + timedelta(seconds=3)))
        self.command("card-4", "card", payload)
        self.ack("card-4", at=app.timestamp(NOW + timedelta(seconds=4)))
        self.assertTrue(store.get(app, "staff", "staff-1")["data"]["card_enabled"])
        self.assertFalse(store.get(app, "staff", "staff-2")["data"]["card_enabled"])
        # The disabled historical card reference must not revoke its new owner.
        before = copy.deepcopy(self.table.items)
        self.command("old-owner-disable", "card", {**payload, "staff_id": "staff-2", "enabled": False}, expected=409)
        self.assertEqual(self.table.items, before)

    def test_card_command_cannot_rebind_active_card_to_wrong_device(self):
        self.create_case()
        self.create_case("2")
        self.post("staff", self.person)
        payload = {"staff_id": "staff-1", "card_id": "CARD-001", "enabled": True}
        self.command("card-1", "card", payload)
        self.ack("card-1")
        self.command("card-other-case", "card", payload, case="case-2", expected=409)
        self.command("card-disable-wrong-case", "card", {**payload, "enabled": False}, case="case-2", expected=409)

    def test_atomic_concurrent_unique_claim_does_not_write_orphan_canonical_exhibit(self):
        def race(items):
            items[("WF#unique", "tag#EX-001")] = {"PK": "WF#unique", "SK": "tag#EX-001", "version": 1,
                                                        "data": {"owner": "other", "active": True}}
        self.table.before_transaction = race
        self.post("exhibit-profiles", self.profile, 409)
        self.assertNotIn(("WF#exhibit-profile", "ex-1"), self.table.items)
        self.assertNotIn(("EXHIBIT#ex-1", "META"), self.table.items)
        self.assertFalse(any(key[0].startswith("WFIDX#exhibit-profile") for key in self.table.items))

    def test_tag_scans_and_access_attempts_are_separate_immutable_producer_assertions(self):
        self.create_case()
        scan = {"scan_id": "scan-1", "device_id": "device-1", "recorded_at": "2026-10-26T11:00:00Z", "source": "device", "scan_type": "exhibit", "tag_id": "EX-UNREGISTERED"}
        self.post("tag-scans", scan)
        self.post("tag-scans", scan, 200)
        self.post("tag-scans", {**scan, "tag_id": "CARD-001"}, 400)
        attempt = {"attempt_id": "access-1", "device_id": "device-1", "recorded_at": scan["recorded_at"], "source": "device", "card_id": "EX-UNREGISTERED", "decision": "denied"}
        self.post("access-attempts", attempt)
        self.post("access-attempts", attempt, 200)
        self.post("access-attempts", {**attempt, "decision": "accepted"}, 409)
        status, body = self.request("GET", "/api/access-attempts", query={"device_id": "device-1"})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["decision"], "denied")
        self.assertEqual(self.request("GET", "/api/access-attempts")[0], 400)

    def test_paginated_query_current_catalog_values_and_context_cursor_validation(self):
        self.create_profile()
        self.create_profile({**self.profile, "exhibit_id": "ex-2", "tag_id": "EX-002"})
        self.request("PATCH", "/api/exhibit-profiles/ex-1", {"name": "Updated current"}, match=1)
        status, first = self.request("GET", "/api/exhibit-profiles", query={"limit": "1"})
        self.assertEqual(status, 200)
        self.assertIsNotNone(first["next_cursor"])
        status, second = self.request("GET", "/api/exhibit-profiles", query={"limit": "1", "cursor": first["next_cursor"]})
        self.assertEqual(status, 200)
        all_items = first["items"] + second["items"]
        self.assertEqual(len({item["exhibit_id"] for item in all_items}), 2)
        self.assertEqual(next(item for item in all_items if item["exhibit_id"] == "ex-1")["name"], "Updated current")
        self.assertEqual(self.request("GET", "/api/staff", query={"cursor": first["next_cursor"]})[0], 400)
        for bad in ("!", "e30=", "bnVsbA==", "W10=", "NDI=", "A" * 3000):
            self.assertEqual(self.request("GET", "/api/exhibit-profiles", query={"cursor": bad})[0], 400)
        self.assertFalse(any(operation == "scan" for operation, _ in self.table.calls))

    def test_audit_device_exhibit_time_index_and_date_half_open(self):
        self.create_case()
        self.create_profile()
        self.command("assign-1", "assignment", {"exhibit_id": "ex-1"})
        self.ack("assign-1")
        query = {"device_id": "device-1", "exhibit_id": "ex-1", "start": "2026-10-26T11:59:59Z", "end": "2026-10-26T12:00:01Z"}
        status, page = self.request("GET", "/api/workflow-activity", query=query)
        self.assertEqual(status, 200, page)
        self.assertEqual({row["status"] for row in page["items"]}, {"pending", "acknowledged"})
        status, page = self.request("GET", "/api/workflow-activity", query={**query, "end": "2026-10-26T12:00:00Z"})
        self.assertEqual(status, 200, page)
        self.assertEqual(page["items"], [])

    def test_profile_only_create_supports_ingress_report_and_explicit_version_reapply(self):
        self.create_case()
        self.create_profile()
        legacy_before = copy.deepcopy(self.table.items[("EXHIBIT#ex-1", "META")])
        assigned = self.command("profile-assign-1", "assignment", {"exhibit_id": "ex-1"})
        self.assertEqual(assigned["item"]["payload"]["profile"]["version"], 1)
        self.ack("profile-assign-1")
        reading = {"reading_id": "profile-reading", "device_id": "device-1", "exhibit_id": "ex-1", "recorded_at": "2026-10-26T11:00:00Z",
                   "temperature_c": 22, "brightness_raw": 1200, "occupied": False, "distance_cm": None, "source": "demo"}
        self.post("readings", reading)
        interval = {"device_id": "device-1", "exhibit_id": "ex-1", "start": "2026-10-26T11:00:00Z", "end": "2026-10-26T11:30:00Z", "source": "demo"}
        self.post("display-history", {**interval, "display_id": "profile-display"})
        self.post("warnings", {**interval, "warning_id": "profile-warning", "warning_type": "temperature", "message": "Synthetic producer warning"})
        query = {"device_id": "device-1", "start": interval["start"], "end": interval["end"]}
        status, original_report = self.request("GET", "/api/reports/event", query=query)
        self.assertEqual(status, 200, original_report)
        self.assertEqual(original_report["summary"]["warning_count"], 1)
        self.assertEqual(original_report["display_history"][0]["exhibit_id"], "ex-1")
        status, _ = self.request("PATCH", "/api/exhibit-profiles/ex-1", {"name": "Edited", "warning_seconds": 45}, match=1)
        self.assertEqual(status, 200)
        case = store.get(app, "showcase", "case-1")["data"]
        self.assertEqual(case["confirmed_exhibit_id"], "ex-1")
        self.assertEqual(case["confirmed_profile_version"], 1)
        self.assertEqual(self.table.items[("EXHIBIT#ex-1", "META")], legacy_before)
        self.assertEqual(self.request("GET", "/api/reports/event", query=query)[1], original_report)
        refreshed = self.command("profile-assign-2", "assignment", {"exhibit_id": "ex-1"})
        self.assertEqual(refreshed["item"]["payload"]["profile"]["version"], 2)
        self.assertEqual(store.get(app, "showcase", "case-1")["data"]["confirmed_profile_version"], 1)
        self.ack("profile-assign-2", at=app.timestamp(NOW + timedelta(seconds=2)))
        self.assertEqual(store.get(app, "showcase", "case-1")["data"]["confirmed_profile_version"], 2)

    def test_known_stale_contact_blocks_new_commands_but_not_identical_retry(self):
        self.create_case()
        initial = self.command("bootstrap", "lighting", {"mode": "On"})["item"]
        self.assertEqual(initial["protocol"], "device-fence-v1")
        self.assertEqual(initial["resource_generation"], 1)
        self.assertEqual(initial["execute_before"], app.timestamp(NOW + timedelta(seconds=300)))
        self.observe()
        self.ack("bootstrap")
        with patch.object(app, "now_utc", return_value=NOW + timedelta(seconds=121)):
            before = copy.deepcopy(self.table.items)
            rejected = self.command("offline", "lighting", {"mode": "Off"}, expected=409)
            self.assertEqual(rejected["error"]["code"], "device_offline")
            self.assertEqual(self.table.items, before)
            self.command("bootstrap", "lighting", {"mode": "On"}, expected=200)
            self.observe("reconnected")
            new = self.command("online-again", "lighting", {"mode": "Off"})
            self.assertGreater(new["item"]["resource_generation"], initial["resource_generation"])

    def test_expiry_keeps_lock_fence_recovery_releases_it_and_late_ack_cannot_apply(self):
        self.create_case()
        original = self.command("abandoned", "lighting", {"mode": "On"})["item"]
        with patch.object(app, "now_utc", return_value=NOW + timedelta(seconds=300)):
            before = copy.deepcopy(self.table.items)
            expired = self.ack("abandoned", at=app.timestamp(app.now_utc()), expected=409)
            self.assertEqual(expired["error"]["code"], "command_expired")
            self.assertEqual(self.table.items, before)
            self.command("blocked", "lighting", {"mode": "Off"}, expected=409)
            recovery = self.recover("abandoned")["item"]
            self.assertEqual(recovery["status"], "recovery_pending")
            self.assertGreater(recovery["recovery"]["fence_generation"], original["resource_generation"])
            self.command("still-blocked", "lighting", {"mode": "Off"}, expected=409)
            self.observe("recovery-status")
            receipt = self.recovery_receipt("abandoned")
            for change, code in (({"outcome": "unknown"}, "recovery_outcome_unknown"),
                                 ({"device_id": "wrong-device"}, "device_mismatch"),
                                 ({"fenced_generation": original["resource_generation"]}, "recovery_mismatch")):
                frozen = copy.deepcopy(self.table.items)
                body = self.post("commands/abandoned/recovery-acknowledgements", {**receipt, **change}, 409)
                self.assertEqual(body["error"]["code"], code)
                self.assertEqual(self.table.items, frozen)
            recovered = self.post("commands/abandoned/recovery-acknowledgements", receipt)["item"]
            self.assertEqual(recovered["status"], "recovered")
            self.assertEqual(recovered["recovery"]["outcome"], "not_applied")
            self.assertIsNone(recovered["acknowledgement_id"])
            self.assertIsNone(store.get(app, "showcase", "case-1")["data"]["lighting_mode"])
            frozen = copy.deepcopy(self.table.items)
            self.post("commands/abandoned/recovery-acknowledgements", receipt, 200)
            self.recover("abandoned", expected=200)
            self.assertEqual(self.table.items, frozen)
            self.post("commands/abandoned/recovery-acknowledgements", {**receipt, "outcome": "applied"}, 409)
            replacement = self.command("replacement", "lighting", {"mode": "Off"})["item"]
            self.assertGreater(replacement["resource_generation"], recovered["recovery"]["fence_generation"])
            frozen = copy.deepcopy(self.table.items)
            self.ack("abandoned", at=app.timestamp(app.now_utc()), expected=409)
            self.assertEqual(self.table.items, frozen)
            self.ack("replacement", at=app.timestamp(app.now_utc()))
            self.assertEqual(store.get(app, "showcase", "case-1")["data"]["lighting_mode"], "Off")

    def test_recovery_rejects_old_or_wrong_observation_and_reconciles_applied_profile_version(self):
        self.create_case()
        self.create_case("2")
        self.create_profile()
        self.observe("old-status")
        self.command("uncertain-assignment", "assignment", {"exhibit_id": "ex-1"})
        self.request("PATCH", "/api/exhibit-profiles/ex-1", {"warning_seconds": 45}, match=1)
        with patch.object(app, "now_utc", return_value=NOW + timedelta(seconds=301)):
            self.recover("uncertain-assignment")
            self.observe("other-status", "device-2")
            for status_id in ("old-status", "other-status"):
                rejected = self.post("commands/uncertain-assignment/recovery-acknowledgements",
                                     self.recovery_receipt("uncertain-assignment", outcome="applied", observed_status_id=status_id), 409)
                self.assertEqual(rejected["error"]["code"], "recovery_observation_mismatch")
            self.observe("recovery-status")
            receipt = self.recovery_receipt("uncertain-assignment", outcome="applied")
            recovered = self.post("commands/uncertain-assignment/recovery-acknowledgements", receipt)["item"]
            self.assertTrue(recovered["recovery"]["confirmed_state_updated"])
            self.assertIsNone(recovered["acknowledgement_id"])
            self.assertEqual(store.get(app, "showcase", "case-1")["data"]["confirmed_profile_version"], 1)
            self.assertFalse(any(key[0].startswith(("DISPLAY#", "COVERAGE#")) for key in self.table.items))

    def test_unknown_card_recovery_keeps_global_reservation_until_known_not_applied(self):
        self.create_case()
        self.post("staff", self.person)
        self.post("staff", {**self.person, "staff_id": "staff-2", "email": "second@example.com"})
        payload = {"staff_id": "staff-1", "card_id": "CARD-RETRY", "enabled": True}
        self.command("card-abandoned", "card", payload)
        self.recover("card-abandoned")
        self.observe("recovery-status")
        self.post("commands/card-abandoned/recovery-acknowledgements", self.recovery_receipt("card-abandoned", outcome="unknown"), 409)
        self.command("other-card", "card", {**payload, "staff_id": "staff-2"}, expected=409)
        self.post("commands/card-abandoned/recovery-acknowledgements", self.recovery_receipt("card-abandoned"))
        self.command("other-card", "card", {**payload, "staff_id": "staff-2"})
        self.assertFalse(store.get(app, "staff", "staff-1")["data"]["card_enabled"])

    def test_fenced_applied_stale_label_recovery_allows_corrective_label_without_false_confirmation(self):
        self.create_case()
        self.create_profile()
        self.create_profile({**self.profile, "exhibit_id": "ex-2", "tag_id": "EX-002"})
        content = {"title": "Old", "collection": "Test", "description": "Synthetic label", "image_media_id": None}
        for exhibit_id in ("ex-1", "ex-2"):
            self.assertEqual(self.request("PUT", "/api/label-drafts/" + exhibit_id, content, match=0)[0], 201)
        self.command("assign-before-label", "assignment", {"exhibit_id": "ex-1"})
        self.ack("assign-before-label")
        self.command("uncertain-old-label", "label", {"exhibit_id": "ex-1", "draft_version": 1})
        self.command("assign-new-exhibit", "assignment", {"exhibit_id": "ex-2"})
        self.ack("assign-new-exhibit", at=app.timestamp(NOW + timedelta(seconds=2)))
        self.recover("uncertain-old-label")
        self.observe("recovery-status")
        result = self.post("commands/uncertain-old-label/recovery-acknowledgements", self.recovery_receipt("uncertain-old-label", outcome="applied"))["item"]
        self.assertTrue(result["recovery"]["state_reconciliation_required"])
        self.assertFalse(result["recovery"]["confirmed_state_updated"])
        case = self.request("GET", "/api/showcases/case-1")[1]["item"]
        self.assertIsNone(case["confirmed_label"])
        self.assertTrue(case["label_reconciliation_required"])
        self.ack("uncertain-old-label", expected=409)
        self.command("corrective-label", "label", {"exhibit_id": "ex-2", "draft_version": 1})
        self.ack("corrective-label", at=app.timestamp(NOW + timedelta(seconds=3)))
        case = self.request("GET", "/api/showcases/case-1")[1]["item"]
        self.assertFalse(case["label_reconciliation_required"])
        self.assertEqual(case["confirmed_label"]["exhibit_id"], "ex-2")

    def test_recovery_transaction_conflict_does_not_release_lock_or_write_receipt(self):
        self.create_case()
        self.command("recover-race", "lighting", {"mode": "On"})
        self.recover("recover-race")
        self.observe("recovery-status")
        self.table.fail_transaction = True
        before = copy.deepcopy(self.table.items)
        self.post("commands/recover-race/recovery-acknowledgements", self.recovery_receipt("recover-race"), 409)
        self.assertEqual(self.table.items, before)
        self.assertEqual(store.get(app, "pending", "showcase#case-1#lighting")["data"]["status"], "recovery_pending")

    def test_legacy_pending_command_is_not_executable_after_bounded_window(self):
        self.create_case()
        self.command("legacy-pending", "lighting", {"mode": "On"})
        current = self.table.items[("WF#command", "legacy-pending")]
        for field in ("execute_before", "resource_generation", "protocol"):
            current["data"].pop(field)
        legacy = copy.deepcopy(current)
        with patch.object(app, "now_utc", return_value=NOW + timedelta(seconds=301)):
            body = self.ack("legacy-pending", at=app.timestamp(app.now_utc()), expected=409)
            self.assertEqual(body["error"]["code"], "command_expired")
            self.assertEqual(self.table.items[("WF#command", "legacy-pending")], legacy)
            self.recover("legacy-pending")
            self.observe("recovery-status")
            self.post("commands/legacy-pending/recovery-acknowledgements", self.recovery_receipt("legacy-pending"))

    def test_card_scan_reference_matches_fresh_attempt_and_is_not_reused(self):
        self.create_case()
        self.create_case("2")
        self.post("staff", self.person)
        scan = {"scan_id": "scan-current", "scan_attempt_id": "attempt-current", "device_id": "device-1", "recorded_at": app.timestamp(NOW),
                "source": "demo", "scan_type": "staff_card", "tag_id": "CARD-SCAN"}
        self.post("tag-scans", scan)
        payload = {"staff_id": "staff-1", "card_id": "CARD-SCAN", "enabled": True, "scan_id": "scan-current", "scan_attempt_id": "attempt-current"}
        self.command("scan-wrong-attempt", "card", {**payload, "scan_attempt_id": "old-attempt"}, expected=409)
        self.command("scan-wrong-device", "card", payload, case="case-2", expected=409)
        self.command("scan-partial", "card", {key: value for key, value in payload.items() if key != "scan_attempt_id"}, expected=400)
        self.post("tag-scans", {**scan, "scan_id": "exhibit-scan", "scan_type": "exhibit", "tag_id": "EX-TAG"})
        self.command("scan-exhibit", "card", {**payload, "scan_id": "exhibit-scan"}, expected=409)
        self.command("scan-good", "card", payload)
        self.ack("scan-good", success=False)
        self.command("scan-reuse", "card", payload, expected=409)
        self.command("scan-good", "card", payload, expected=200)
        with patch.object(app, "now_utc", return_value=NOW + timedelta(seconds=121)):
            self.observe("fresh-contact")
            rejected = self.command("scan-old", "card", payload, expected=409)
            self.assertEqual(rejected["error"]["code"], "scan_stale")


@unittest.skipUnless(boto3, "Run with the project's boto3 verification environment")
class WorkflowSDKTests(unittest.TestCase):
    def test_native_complex_transaction_is_serialized_once_by_resource_client(self):
        table = boto3.resource("dynamodb", region_name="us-east-1", aws_access_key_id="fake-key", aws_secret_access_key="fake-secret").Table("wire-workflow")
        captured = []
        class Captured(Exception):
            pass
        def capture(params, **_kwargs):
            captured.append(json.loads(params["body"]))
            raise Captured()
        table.meta.client.meta.events.register_first("before-call.dynamodb.TransactWriteItems", capture)
        with patch.object(app, "_table", table), patch.object(app, "now_utc", return_value=NOW):
            previous = store.make(app, "showcase", "wire-case", {"device_id": "wire-device", "confirmed_exhibit_id": None})
            replacement = store.make(app, "showcase", "wire-case", {"device_id": "wire-device", "confirmed_exhibit_id": "ex-1", "brightness_percent": Decimal("12.5"), "label": {"title": "Artifact", "image_media_id": None}}, previous)
            receipt = store.make(app, "acknowledgement", "wire-ack", {"device_id": "wire-device", "success": True, "source": "demo"})
            actions = [store.put_action(app, replacement, previous), store.put_action(app, receipt), *store.index_actions(app, receipt)]
            with self.assertRaises(Captured):
                store.transact(app, actions)
        puts = [item["Put"] for item in captured[0]["TransactItems"]]
        self.assertEqual(puts[0]["Item"]["PK"], {"S": "WF#showcase"})
        self.assertEqual(puts[0]["Item"]["data"]["M"]["brightness_percent"], {"N": "12.5"})
        self.assertEqual(puts[0]["Item"]["data"]["M"]["label"]["M"]["image_media_id"], {"NULL": True})
        self.assertEqual(puts[1]["Item"]["data"]["M"]["success"], {"BOOL": True})
        self.assertEqual(puts[0]["ExpressionAttributeValues"][":version"], {"N": "1"})
        self.assertTrue(all(row["TableName"] == "wire-workflow" for row in puts))

    def test_real_botocore_conditional_cancellation_maps_to_conflict(self):
        from botocore.stub import Stubber
        table = boto3.resource("dynamodb", region_name="us-east-1", aws_access_key_id="fake-key", aws_secret_access_key="fake-secret").Table("wire-workflow")
        with patch.object(app, "_table", table), patch.object(app, "now_utc", return_value=NOW):
            previous = store.make(app, "staff", "staff-1", {"staff_id": "staff-1", "card_enabled": False})
            replacement = store.make(app, "staff", "staff-1", {"staff_id": "staff-1", "card_enabled": True}, previous)
            actions = [store.put_action(app, replacement, previous)]
            with Stubber(table.meta.client) as stub:
                stub.add_client_error("transact_write_items", service_error_code="TransactionCanceledException",
                                      service_message="Conditional write failed", http_status_code=400,
                                      modeled_fields={"CancellationReasons": [{"Code": "ConditionalCheckFailed"}]})
                with self.assertRaises(app.APIError) as caught:
                    store.transact(app, actions)
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(caught.exception.code, "version_conflict")
                stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
