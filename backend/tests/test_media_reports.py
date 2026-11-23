"""Image isolation, immutable saved reports and real date/coverage semantics."""
import base64
import copy
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

try:
    import boto3
    from botocore.config import Config
    from botocore.response import StreamingBody
    from botocore.stub import Stubber
except ImportError:
    boto3 = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app
import analytics
import media_reports as module


class S3Failure(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class S3:
    def __init__(self):
        self.objects = {}
        self.calls = []

    def generate_presigned_post(self, **kwargs):
        self.calls.append(("post", copy.deepcopy(kwargs)))
        return {"url": "https://private.example.test/", "fields": {"key": kwargs["Key"], **kwargs["Fields"], "policy": "temporary-policy"}}

    def generate_presigned_url(self, operation, **kwargs):
        self.calls.append((operation, copy.deepcopy(kwargs)))
        return "https://private.example.test/final?temporary-signature"

    def put_object(self, **kwargs):
        self.calls.append(("put", copy.deepcopy(kwargs)))
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise S3Failure("PreconditionFailed")
        self.objects[key] = {"body": kwargs["Body"], "type": kwargs["ContentType"]}
        return {}

    def head_object(self, **kwargs):
        self.calls.append(("head", copy.deepcopy(kwargs)))
        obj = self.objects.get(kwargs["Key"])
        if not obj:
            raise S3Failure("NoSuchKey")
        return {"ContentLength": len(obj["body"]), "ContentType": obj["type"], "ETag": hashlib.md5(obj["body"]).hexdigest()}

    def get_object(self, **kwargs):
        self.calls.append(("get", copy.deepcopy(kwargs)))
        obj = self.objects.get(kwargs["Key"])
        if not obj:
            raise S3Failure("NoSuchKey")
        if kwargs["IfMatch"] != hashlib.md5(obj["body"]).hexdigest():
            raise S3Failure("PreconditionFailed")
        end = int(kwargs["Range"].split("-")[1])
        return {"Body": io.BytesIO(obj["body"][:end + 1]), "ContentType": obj["type"]}


class Records:
    """Store contract fake: canonical immutable creation, optimistic replacement."""
    def __init__(self):
        self.items = {}
        self.calls = []

    def get(self, api, kind, record_id, required=True):
        self.calls.append(("get", kind, record_id))
        item = self.items.get((kind, record_id))
        if item is None and required:
            raise api.APIError(404, "not_found", "Missing record.")
        return copy.deepcopy(item)

    def create(self, api, kind, record_id, data):
        self.calls.append(("create", kind, record_id))
        if (kind, record_id) in self.items:
            raise api.APIError(409, "id_conflict", "Identifier exists.")
        at = api.timestamp(api.now_utc())
        item = {"data": copy.deepcopy(data), "version": 1, "created_at": at, "updated_at": at}
        self.items[kind, record_id] = item
        return copy.deepcopy(item), True

    def replace(self, api, kind, record_id, data, expected_version):
        self.calls.append(("replace", kind, record_id))
        item = self.items[kind, record_id]
        if item["version"] != expected_version:
            raise api.APIError(409, "version_conflict", "Version changed.")
        item = {**item, "data": copy.deepcopy(data), "version": item["version"] + 1, "updated_at": api.timestamp(api.now_utc())}
        self.items[kind, record_id] = item
        return copy.deepcopy(item)

    def list_records(self, api, kind, query):
        self.calls.append(("list", kind, query))
        return {"items": [module.store.public(item) for (candidate, _id), item in self.items.items() if candidate == kind],
                "next_cursor": None, "consistency": "strong", "page_limit": 20}


class MediaReportTests(unittest.TestCase):
    def setUp(self):
        self.records, self.s3 = Records(), S3()
        patches = [patch.dict(os.environ, {"ASSET_BUCKET": "private-test-bucket"}), patch.object(module, "_s3", self.s3),
                   patch.object(app, "now_utc", return_value=datetime(2026, 10, 26, 12, tzinfo=timezone.utc))]
        patches += [patch.object(module.store, name, getattr(self.records, name)) for name in ("get", "create", "replace", "list_records")]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8ksAAAAASUVORK5CYII=")
        self.media = {"media_id": "image-1", "content_type": "image/png", "size_bytes": len(self.png)}
        self.query = {"device_id": "device-1", "start": "2026-10-25T00:00:00Z", "end": "2026-10-25T01:00:00Z"}

    def decode(self, response):
        return json.loads(response["body"])

    def error(self, status, callback, *args, **kwargs):
        with self.assertRaises(app.APIError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def stage(self):
        result = self.decode(module.upload_request(app, self.media))
        key = result["upload"]["fields"]["key"]
        self.s3.objects[key] = {"body": self.png, "type": "image/png"}
        return key

    def test_exact_signed_post_policy_and_same_id_metadata_retry(self):
        first = module.upload_request(app, self.media)
        again = module.upload_request(app, dict(self.media))
        self.assertEqual(first["statusCode"], 201)
        self.assertEqual(again["statusCode"], 200)
        self.assertEqual(self.decode(first)["upload"]["fields"]["key"], self.decode(again)["upload"]["fields"]["key"])
        policy = self.s3.calls[0][1]
        self.assertEqual(policy["Conditions"], [{"Content-Type": "image/png"}, {"x-amz-server-side-encryption": "AES256"},
                                               ["content-length-range", len(self.png), len(self.png)]])
        self.assertEqual(policy["ExpiresIn"], 300)
        self.assertTrue(policy["Key"].startswith("media/staging/image-1/"))
        self.error(409, module.upload_request, app, {**self.media, "size_bytes": 100})
        self.assertEqual(len(self.records.items), 1)

    def test_untrusted_input_rejected_before_storage_and_never_external_fetch(self):
        for body in ({**self.media, "content_type": "image/svg+xml"}, {**self.media, "size_bytes": 0},
                     {**self.media, "size_bytes": 1_048_577},
                     {**self.media, "size_bytes": True}, {**self.media, "size_bytes": 1.2},
                     {**self.media, "url": "https://untrusted.test/payload"}, {**self.media, "media_id": "../escape"}):
            self.error(400, module.upload_request, app, body)
        self.assertEqual(self.records.items, {})
        self.assertEqual(self.s3.calls, [])

    def test_complete_is_immutable_even_if_signed_staging_form_replayed(self):
        key = self.stage()
        ready = self.decode(module.complete_media(app, "image-1"))["item"]
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["sha256"], hashlib.sha256(self.png).hexdigest())
        self.assertNotIn("staging_key", ready)
        final_key = self.records.items["media", "image-1"]["data"]["final_key"]
        self.assertNotEqual(key, final_key)
        self.assertEqual(self.s3.objects[final_key]["body"], self.png)
        self.s3.objects[key] = {"body": b"replacement", "type": "image/png"}
        self.assertEqual(self.decode(module.complete_media(app, "image-1"))["item"], ready)
        result = self.decode(module.get_media(app, "image-1"))
        self.assertIn("download", result)
        self.assertEqual(self.s3.calls[-1][1]["Params"]["Key"], final_key)
        self.assertEqual(self.s3.objects[final_key]["body"], self.png)
        self.assertNotIn("upload", self.decode(module.upload_request(app, self.media)))
        puts = [params for operation, params in self.s3.calls if operation == "put"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["IfNoneMatch"], "*")

    def test_no_download_until_ready_missing_upload_size_type_signature_and_etag_checked(self):
        module.upload_request(app, self.media)
        self.assertNotIn("download", self.decode(module.get_media(app, "image-1")))
        self.assertEqual(self.error(409, module.complete_media, app, "image-1").code, "upload_incomplete")
        for code in ("403", "AccessDenied"):
            with patch.object(self.s3, "head_object", side_effect=S3Failure(code)):
                self.assertEqual(self.error(409, module.complete_media, app, "image-1").code, "upload_incomplete")
        key = self.records.items["media", "image-1"]["data"]["staging_key"]
        for raw, content_type in ((b"", "image/png"), (self.png + b"x", "image/png"),
                                  (self.png, "image/jpeg"), (b"x" * len(self.png), "image/png")):
            self.s3.objects[key] = {"body": raw, "type": content_type}
            self.assertEqual(self.error(400, module.complete_media, app, "image-1").code, "invalid_asset")
            self.assertEqual(self.records.items["media", "image-1"]["data"]["status"], "pending")
        self.s3.objects[key] = {"body": self.png, "type": "image/png"}
        with patch.object(self.s3, "get_object", side_effect=S3Failure("PreconditionFailed")):
            self.assertEqual(self.error(409, module.complete_media, app, "image-1").code, "upload_changed")
        self.assertFalse(any(key.startswith("media/final/") for key in self.s3.objects))

    def test_conditional_final_put_retry_checks_existing_bytes_not_just_metadata(self):
        self.stage()
        final_key = "media/final/image-1/" + hashlib.sha256(self.png).hexdigest()
        self.s3.objects[final_key] = {"body": b"x" * len(self.png), "type": "image/png"}
        self.error(503, module.complete_media, app, "image-1")
        self.assertEqual(self.records.items["media", "image-1"]["data"]["status"], "pending")
        self.s3.objects[final_key]["body"] = self.png
        self.assertEqual(self.decode(module.complete_media(app, "image-1"))["item"]["status"], "ready")

    def test_object_content_type_is_checked_after_conditional_get(self):
        key = self.stage()
        original_get = self.s3.get_object

        def replace_metadata(**kwargs):
            self.s3.objects[key]["type"] = "image/jpeg"
            # The unchanged bytes still satisfy the conditional ETag check.
            return original_get(**kwargs)

        with patch.object(self.s3, "get_object", side_effect=replace_metadata):
            self.assertEqual(self.error(400, module.complete_media, app, "image-1").code, "invalid_asset")
        self.assertEqual(self.records.items["media", "image-1"]["data"]["status"], "pending")
        self.assertFalse(any(operation == "put" for operation, _ in self.s3.calls))

    def test_max_size_png_finalizes_and_one_byte_fails_signature_check(self):
        one_byte = {**self.media, "media_id": "one-byte", "size_bytes": 1}
        key = self.decode(module.upload_request(app, one_byte))["upload"]["fields"]["key"]
        self.s3.objects[key] = {"body": b"\x89", "type": "image/png"}
        self.assertEqual(self.error(400, module.complete_media, app, "one-byte").code, "invalid_asset")
        chunk_type_and_keyword = b"tEXtComment\0"
        padding = module.MAX_IMAGE_BYTES - len(self.png) - len(chunk_type_and_keyword) - 8
        text_chunk = chunk_type_and_keyword + b"x" * padding
        chunk = (len(text_chunk) - 4).to_bytes(4, "big") + text_chunk + zlib.crc32(text_chunk).to_bytes(4, "big")
        png = self.png[:33] + chunk + self.png[33:]
        self.assertEqual(len(png), module.MAX_IMAGE_BYTES)
        largest = {**self.media, "media_id": "max-size", "size_bytes": len(png)}
        key = self.decode(module.upload_request(app, largest))["upload"]["fields"]["key"]
        self.s3.objects[key] = {"body": png, "type": "image/png"}
        item = self.decode(module.complete_media(app, "max-size"))["item"]
        self.assertEqual(item["sha256"], hashlib.sha256(png).hexdigest())

    def timeline(self):
        def item(kind, data):
            return {"entity_type": kind, "data": {"device_id": "device-1", "source": "demo", **data}, "received_at": "2026-10-25T01:01:00Z"}
        return [item("proximity_event", {"proximity_event_id": "one", "start": "2026-10-25T00:01:00Z", "end": "2026-10-25T00:11:00Z"}),
                item("coverage", {"coverage_id": "coverage", "start": self.query["start"], "end": "2026-10-25T00:30:00Z", "streams": list(analytics.STREAMS)}),
                item("display", {"display_id": "display", "exhibit_id": "exhibit-1", "start": self.query["start"], "end": self.query["end"]})]

    def test_snapshot_freezes_data_and_profiles_repeat_does_not_recompute_or_create(self):
        self.records.create(app, "exhibit-profile", "exhibit-1", {"exhibit_id": "exhibit-1", "name": "Original name"})
        body = {"report_id": "report-1", **self.query}
        with patch.object(analytics, "read_timeline", return_value=self.timeline()) as read:
            first = module.create_snapshot(app, body)
            initial = self.decode(module.get_snapshot(app, "report-1", "json"))
            self.records.items["exhibit-profile", "exhibit-1"]["data"]["name"] = "New current name"
            read.return_value = []
            repeated = module.create_snapshot(app, body)
            self.assertEqual(read.call_count, 1)
        self.assertEqual(first["statusCode"], 201)
        self.assertEqual(repeated["statusCode"], 200)
        self.assertEqual(initial, self.decode(module.get_snapshot(app, "report-1", "json")))
        self.assertEqual(initial["report"]["summary"]["recorded_occupied_seconds"], 600)
        self.assertEqual(initial["report"]["summary"]["missing_seconds"], 1800)
        self.assertEqual(initial["profile_context"]["exhibits"][0]["profile"]["name"], "Original name")
        self.assertFalse(initial["profile_context"]["historical_settings_known"])
        self.error(409, module.create_snapshot, app, {**body, "title": "Changed"})
        puts = [params for operation, params in self.s3.calls if operation == "put"]
        self.assertEqual(len(puts), 1)
        self.assertTrue(puts[0]["Key"].startswith("report-snapshots/report-1/"))
        self.assertEqual(puts[0]["IfNoneMatch"], "*")

    def test_section_selection_always_keeps_missing_lower_bounds_and_snapshot_csv_metadata(self):
        body = {"report_id": "report-selected", "title": "=unsafe spreadsheet title", **self.query, "sections": ["environment"]}
        with patch.object(analytics, "read_timeline", return_value=self.timeline()), patch.object(module, "frozen_profiles", return_value={
                "captured_at": "capture", "historical_settings_known": False, "exhibits": []}):
            module.create_snapshot(app, body)
        result = self.decode(module.get_snapshot(app, "report-selected", "json"))
        self.assertEqual(result["sections"], ["environment", "missing"])
        self.assertNotIn("buckets", result["report"])
        self.assertNotIn("proximity_events", result["report"])
        self.assertNotIn("display_history", result["report"])
        self.assertEqual(result["report"]["summary"]["totals_interpretation"], "recorded_lower_bounds")
        self.assertIn("missing_data", result["report"])
        self.assertIn("limitations", result["report"])
        csv_response = module.get_snapshot(app, "report-selected", "csv")
        rows = list(csv.DictReader(io.StringIO(csv_response["body"])))
        summary = json.loads(next(row["detail"] for row in rows if row["section"] == "summary"))
        metadata = summary["data_quality"]["snapshot"]
        self.assertEqual(metadata["report_id"], "report-selected")
        self.assertEqual(metadata["title"], body["title"])
        self.assertIn("captured_at", metadata)
        self.assertIn("profile_context", metadata)
        self.assertTrue(any(row["section"] == "missing_data_proximity" for row in rows))
        self.assertFalse(any(row["section"] == "heatmap" for row in rows))
        self.assertTrue(all(not str(cell).lstrip().startswith(("=", "+", "-", "@")) for row in rows for cell in row.values()))

    def test_snapshot_list_masks_internal_object_keys_and_rejects_unknown_query(self):
        with patch.object(analytics, "read_timeline", return_value=[]):
            module.create_snapshot(app, {"report_id": "list-report", **self.query})
        page = self.decode(module.route(app, "GET", "/api/report-snapshots", {}, {}, {}))
        self.assertEqual(page["items"][0]["report_id"], "list-report")
        self.assertNotIn("object_key", page["items"][0])
        self.assertNotIn("request", page["items"][0])
        self.error(400, module.route, app, "GET", "/api/report-snapshots", {"unsupported": "no"}, {}, {})

    def test_snapshot_hash_and_size_caps_fail_explicitly(self):
        with patch.object(analytics, "read_timeline", return_value=[]):
            module.create_snapshot(app, {"report_id": "cap-report", **self.query})
        item = self.records.items["report-snapshot", "cap-report"]["data"]
        obj = self.s3.objects[item["object_key"]]
        original = obj["body"]
        obj["body"] = b"x" * len(original)
        self.error(503, module.get_snapshot, app, "cap-report", "json")
        obj["body"] = original
        with patch.object(analytics, "MAX_RESPONSE_BYTES", 20):
            self.error(503, module.get_snapshot, app, "cap-report", "json")
        with patch.object(analytics, "read_timeline", return_value=[]), patch.object(analytics, "MAX_RESPONSE_BYTES", 20):
            self.error(413, module.create_snapshot, app, {"report_id": "too-large", **self.query})
        self.assertNotIn(("report-snapshot", "too-large"), self.records.items)

    def test_activity_inclusive_hong_kong_dates_and_real_dst_boundaries(self):
        config, selection = module.activity_query(app, {"device_id": "device-1", "start_date": "2026-10-24", "end_date": "2026-10-25"})
        self.assertEqual(app.timestamp(config[1]), "2026-10-23T16:00:00.000000Z")
        self.assertEqual(app.timestamp(config[2]), "2026-10-25T16:00:00.000000Z")
        self.assertTrue(selection["end_date_inclusive"])
        config, _selection = module.activity_query(app, {"device_id": "device-1", "start_date": "2026-03-08", "end_date": "2026-03-08", "timezone": "America/New_York"})
        self.assertEqual((config[2] - config[1]).total_seconds(), 23 * 3600)
        config, _selection = module.activity_query(app, {"device_id": "device-1", "start_date": "2026-10-25", "end_date": "2026-10-25", "timezone": "Europe/London"})
        self.assertEqual((config[2] - config[1]).total_seconds(), 25 * 3600)

    def test_activity_coverage_semantics_never_claims_exhibit_counts_or_quiet_missing(self):
        query = {"device_id": "device-1", "start_date": "2026-10-25", "end_date": "2026-10-25", "timezone": "UTC"}
        with patch.object(analytics, "read_timeline", return_value=self.timeline()):
            result = self.decode(module.route(app, "GET", "/api/activity", query, {}, {}))
        self.assertEqual(result["buckets"][0]["status"], "recorded_activity")
        self.assertEqual(result["buckets"][1]["status"], "confirmed_quiet")
        self.assertEqual(result["buckets"][2]["status"], "missing")
        self.assertIsNone(result["buckets"][2]["recorded_occupied_seconds"])
        self.assertEqual(result["summary"]["recorded_occupied_seconds"], 600)
        self.assertEqual(len(result["buckets"]), 96)
        self.assertEqual(module.activity_query(app, {**query, "exhibit_id": "exhibit-1"})[0][0], "device-1")
        self.error(400, module.activity_query, app, {**query, "exhibit_id": "../invalid"})
        self.error(400, module.activity_query, app, {**query, "start_date": "2026-10-26", "end_date": "2026-10-25"})
        self.error(400, module.activity_query, app, {**query, "start_date": "2026-02-30"})
        self.error(400, module.activity_query, app, {**query, "start_date": "2026-09-01", "end_date": "2026-10-25"})
        self.error(400, module.activity_query, app, {**query, "start_date": "2026-10-24", "interval_minutes": "1"})

    def test_report_identity_requires_unique_sections_and_exclusive_selector(self):
        base = {"report_id": "identity", **self.query}
        for body in ({**base, "event_id": "event-1"}, {**base, "sections": ["history", "history"]},
                     {**base, "sections": []}, {**base, "sections": ["unknown"]}, {**base, "interval_minutes": True},
                     {**base, "timezone": "Unknown/Time"}, {**base, "exhibit_id": "../invalid"}):
            self.error(400, module.report_identity, app, body)
        first = module.report_identity(app, base)
        again = module.report_identity(app, {**base, "start": "2026-10-25T00:00:00.000Z", "sections": list(reversed(module.SECTIONS))})
        self.assertEqual(first, again)


class DeploymentSafetyTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("media_report_deploy", ROOT / "scripts" / "deploy.py")
        self.deploy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.deploy)

    def test_package_includes_explicit_target_dependencies_without_loading_them_locally(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); backend = root / "backend"; dependencies = root / "dependencies"
            backend.mkdir(); (dependencies / "cryptography").mkdir(parents=True)
            for name in ("app.py", "index.html", "openapi.json"):
                (backend / name).write_text("source")
            (dependencies / "cryptography" / "__init__.py").write_text("# target package")
            (dependencies / "_cffi_backend.cpython-313-aarch64-linux-gnu.so").write_bytes(b"target-native-module")
            archive = root / "backend.zip"
            digest = self.deploy.package_backend(backend, archive, dependencies)
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(set(bundle.namelist()), {"app.py", "index.html", "openapi.json",
                    "cryptography/__init__.py", "_cffi_backend.cpython-313-aarch64-linux-gnu.so"})
                self.assertEqual(bundle.read("_cffi_backend.cpython-313-aarch64-linux-gnu.so"), b"target-native-module")
            self.assertEqual(digest, self.deploy.package_backend(backend, archive, dependencies))
            (dependencies / "app.py").write_text("collision")
            with self.assertRaisesRegex(ValueError, "collides"):
                self.deploy.package_backend(backend, archive, dependencies)
            (dependencies / "app.py").unlink()
            (dependencies / "unsafe").symlink_to(backend / "app.py")
            with self.assertRaisesRegex(ValueError, "symlinked"):
                self.deploy.package_backend(backend, archive, dependencies)
            with self.assertRaisesRegex(ValueError, "dependencies are missing"):
                self.deploy.package_backend(backend, archive, root / "missing")

    def test_s3_iam_narrow_prefixes_and_simulator_template_preserved(self):
        template = json.loads((ROOT / "infra" / "template.json").read_text())
        role = template["Resources"]["ApiRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        permissions = [statement for statement in role if any(str(action).startswith("s3:") for action in statement.get("Action", []))]
        self.assertEqual(len(permissions), 1)
        self.assertEqual(permissions[0]["Action"], ["s3:GetObject", "s3:PutObject"])
        self.assertEqual(permissions[0]["Resource"], [{"Fn::Sub": "arn:${AWS::Partition}:s3:::${ArtifactBucket}/media/*"},
                                                    {"Fn::Sub": "arn:${AWS::Partition}:s3:::${ArtifactBucket}/report-snapshots/*"}])
        instance = template["Resources"]["SimulatorInstance"]["Properties"]
        self.assertEqual(instance["NetworkInterfaces"][0]["GroupSet"], [{"Ref": "SimulatorSecurityGroup"}])
        self.assertEqual(instance["ImageId"], {"Ref": "LatestAmiId"})
        self.assertEqual(template["Resources"]["ApiFunction"]["Properties"]["Environment"]["Variables"]["ASSET_BUCKET"], {"Ref": "ArtifactBucket"})

    def test_change_set_refuses_any_instance_change_even_no_replacement(self):
        self.deploy.reject_ec2_changes([{"ResourceChange": {"LogicalResourceId": "ApiFunction", "Replacement": "False"}}])
        with self.assertRaises(ValueError):
            self.deploy.reject_ec2_changes([{"ResourceChange": {"LogicalResourceId": "SimulatorInstance", "Replacement": "False"}}])

    def test_change_set_checks_later_pages_for_instance_change(self):
        class Aws:
            def call(self, *args):
                if "--next-token" in args:
                    return {"Changes": [{"ResourceChange": {"LogicalResourceId": "SimulatorInstance", "Replacement": "True"}}]}
                return {"Changes": [{"ResourceChange": {"LogicalResourceId": "ApiFunction"}}], "NextToken": "page-two"}
        preview = self.deploy.change_set_preview(Aws(), "arn:fake")
        self.assertEqual(len(preview["Changes"]), 2)
        with self.assertRaises(ValueError):
            self.deploy.reject_ec2_changes(preview["Changes"])

    def test_cors_exact_origin_preserves_unrelated_rules_and_has_no_wildcard_origin(self):
        class Aws:
            def __init__(self):
                self.calls = []
            def call(self, *args):
                self.calls.append(args)
                return {"CORSRules": [{"ID": "Unrelated", "AllowedOrigins": ["https://other.example.test"]}]}
        aws = Aws()
        self.deploy.ensure_media_cors(aws, "bucket", "account", "https://frontend.example.test")
        config = json.loads(aws.calls[-1][-1])["CORSRules"]
        self.assertEqual(config[0]["ID"], "Unrelated")
        self.assertEqual(config[-1]["AllowedOrigins"], ["https://frontend.example.test", "http://localhost:8000"])
        for origin in ("*", "http://bad.test", "https://bad.test/path", "https://user:pass@bad.test"):
            with self.assertRaises(ValueError):
                self.deploy.ensure_media_cors(aws, "bucket", "account", origin)

    def test_prepare_only_outputs_concrete_change_set_and_never_executes_or_updates_parameters(self):
        class Aws:
            def __init__(self):
                self.calls, self.parameters = [], None
            def call(self, *args):
                self.calls.append(args)
                if args[:2] == ("sts", "get-caller-identity"):
                    return {"Account": "835822826851"}
                if args[:2] == ("cloudformation", "describe-stacks"):
                    return {"Stacks": [{"StackId": "stack-id", "Outputs": [{"OutputKey": "InstanceId", "OutputValue": "i-existing"}]}]}
                if args[:2] == ("ec2", "describe-instances"):
                    return {"Reservations": [{"Instances": [{"SecurityGroups": [{"GroupId": "sg-backend"}, {"GroupId": "sg-frontend"}]}]}]}
                if args[:2] == ("cloudformation", "create-change-set"):
                    path = args[args.index("--parameters") + 1][len("file://"):]
                    self.parameters = json.loads(Path(path).read_text())
                    return {"Id": "arn:aws:cloudformation:us-east-1:835822826851:changeSet/reviewable/test"}
                if args[:2] == ("cloudformation", "describe-change-set"):
                    return {"Status": "CREATE_COMPLETE", "Changes": [{"ResourceChange": {"LogicalResourceId": "ApiFunction", "Replacement": "False"}}]}
                return {}
        aws = Aws()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "private.json"
            state.write_text(json.dumps({"stack_name": "ee3070-reshow-demo", "region": "us-east-1", "account_id": "835822826851", "bearer_token": "fake-unit-token-" + "x" * 32}))
            output = Path(directory) / "preview.json"
            args = self.deploy.parser().parse_args(["--state-file", str(state), "--output-file", str(output), "--prepare-only"])
            # Packaging and dependency validation are tested independently above.
            with (
                patch.object(self.deploy, "Aws", return_value=aws),
                patch.object(self.deploy, "package_backend", return_value="a" * 64),
                patch.object(self.deploy, "ensure_artifact_bucket"),
                patch.object(self.deploy, "ensure_media_cors"),
                patch.object(self.deploy.shutil, "which", return_value="aws"),
                patch("sys.stdout", new=io.StringIO()),
            ):
                self.deploy.deploy(args)
            preview = json.loads(output.read_text())
            self.assertEqual(preview["change_set_status"], "CREATE_COMPLETE")
            self.assertIn("changeSet/reviewable", preview["change_set_id"])
            self.assertEqual(preview["instance_network_before"]["security_group_ids"], ["sg-backend", "sg-frontend"])
            self.assertNotIn("fake-unit-token", output.read_text())
        self.assertIn({"ParameterKey": "LatestAmiId", "UsePreviousValue": True}, aws.parameters)
        self.assertFalse(any("execute-change-set" in args for args in aws.calls))
        self.assertFalse(any(args[0] == "ssm" for args in aws.calls))


@unittest.skipUnless(boto3, "Use the existing boto3 environment for real S3 SDK serialization checks")
class MediaSDKTests(unittest.TestCase):
    def client(self):
        return boto3.client("s3", region_name="us-east-1", aws_access_key_id="fake-unit-key",
                            aws_secret_access_key="fake-unit-secret", config=Config(signature_version="s3v4"))

    def test_real_presigned_post_exact_key_length_and_type_conditions(self):
        client = self.client()
        now = datetime(2026, 11, 10, 4, tzinfo=timezone.utc)
        metadata = {"media_id": "sdk-image", "content_type": "image/png", "size_bytes": 68}
        item = {"data": {**metadata, "status": "pending", "staging_key": "media/staging/sdk-image/random"},
                "version": 1, "created_at": "capture", "updated_at": "capture"}
        with patch.object(module, "storage", return_value=(client, "private-test-bucket")), \
             patch.object(module.store, "get", return_value=item), \
             patch("botocore.signers.get_current_datetime", side_effect=[now, now + timedelta(seconds=301)]):
            result = json.loads(module.upload_request(app, metadata)["body"])
            refreshed = json.loads(module.upload_request(app, metadata)["body"])
        fields = result["upload"]["fields"]
        policy = json.loads(base64.b64decode(fields["policy"]))
        refreshed_policy = json.loads(base64.b64decode(refreshed["upload"]["fields"]["policy"]))
        self.assertIn({"key": "media/staging/sdk-image/random"}, policy["conditions"])
        self.assertIn({"Content-Type": "image/png"}, policy["conditions"])
        self.assertIn(["content-length-range", 68, 68], policy["conditions"])
        self.assertNotIn("media/final/", json.dumps(policy))
        self.assertEqual(policy["expiration"], "2026-11-10T04:05:00Z")
        self.assertEqual(refreshed_policy["expiration"], "2026-11-10T04:10:01Z")
        self.assertEqual(refreshed["upload"]["fields"]["key"], fields["key"])

    def test_real_sdk_validates_conditional_put_and_bounded_etag_get(self):
        client = self.client()
        raw = b"verified-bytes"
        digest = hashlib.sha256(raw).hexdigest()
        with Stubber(client) as stubber:
            stubber.add_response("put_object", {}, {"Bucket": "private-test-bucket", "Key": "media/final/sdk/hash", "Body": raw,
                "ContentType": "image/png", "ServerSideEncryption": "AES256", "Metadata": {"sha256": digest}, "IfNoneMatch": "*"})
            module.immutable_object(app, client, "private-test-bucket", "media/final/sdk/hash", raw, "image/png")
            stubber.add_response("head_object", {"ContentLength": len(raw), "ContentType": "image/png", "ETag": '"etag"'},
                                 {"Bucket": "private-test-bucket", "Key": "media/final/sdk/hash"})
            stubber.add_response("get_object", {"Body": StreamingBody(io.BytesIO(raw), len(raw)), "ContentType": "image/png"},
                                 {"Bucket": "private-test-bucket", "Key": "media/final/sdk/hash", "IfMatch": '"etag"', "Range": "bytes=0-1048576"})
            self.assertEqual(module.bounded_object(app, client, "private-test-bucket", "media/final/sdk/hash", module.MAX_IMAGE_BYTES,
                            expected_size=len(raw), expected_type="image/png", expected_hash=digest), raw)
            stubber.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
