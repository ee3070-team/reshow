"""Current-role authorization, resumable enrollment and concurrent admin guards.

Gateway JWT cryptography is an AWS integration check, not simulated by these
tests. The local cases exercise the additional purpose/client/claim checks and
the application authorization path after a gateway-verified request.
"""
import base64
import copy
import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app
import auth
import accounts
import media_reports
import workflow_store as store
import workflows
import staff_reads
from test_media_reports import S3
from test_workflows import WorkflowTable, Conflict

NOW = datetime(2026, 10, 27, 4, tzinfo=timezone.utc)
TOKEN = "synthetic-producer-" + "x" * 32
HARDWARE_TOKEN = "rshw_hw_v1." + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
HARDWARE_DEVICE = "fixture-mega-01"
ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/test-pool"


class AuthTable(WorkflowTable):
    def transact_write_items(self, **kwargs):
        actions = kwargs["TransactItems"]
        keys = []
        for action in actions:
            operation = action.get("Put") or action["ConditionCheck"]
            item = operation.get("Item") or operation["Key"]
            keys.append((item["PK"], item["SK"]))
        self.assert_unique(keys)
        if self.before_transaction:
            hook, self.before_transaction = self.before_transaction, None
            hook(self.items)
        for action, key in zip(actions, keys):
            operation = action.get("Put") or action["ConditionCheck"]
            old = self.items.get(key)
            condition = operation["ConditionExpression"]
            if condition == "attribute_not_exists(PK)":
                if old:
                    raise Conflict()
            elif condition == "#version = :version":
                if old is None or old["version"] != operation["ExpressionAttributeValues"][":version"]:
                    raise Conflict()
            else:
                raise AssertionError(condition)
        self.calls.append(("transact_write_items", copy.deepcopy(kwargs)))
        for action, key in zip(actions, keys):
            if "Put" in action:
                self.items[key] = copy.deepcopy(action["Put"]["Item"])


class ProviderError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class Cognito:
    def __init__(self):
        self.users, self.calls = {}, []

    def admin_get_user(self, **kwargs):
        self.calls.append(("get", kwargs))
        if kwargs["Username"] not in self.users:
            raise ProviderError("UserNotFoundException")
        return copy.deepcopy(self.users[kwargs["Username"]])

    def admin_create_user(self, **kwargs):
        self.calls.append(("create", copy.deepcopy(kwargs)))
        if kwargs.get("MessageAction") == "RESEND":
            return {"User": self.users[kwargs["Username"]]}
        if kwargs["Username"] in self.users:
            raise ProviderError("UsernameExistsException")
        value = {"Username": kwargs["Username"], "UserStatus": "FORCE_CHANGE_PASSWORD",
                 "Attributes": [*kwargs["UserAttributes"], {"Name": "sub", "Value": "subject-" + str(len(self.users))}]}
        self.users[kwargs["Username"]] = value
        return {"User": copy.deepcopy(value)}

    def admin_disable_user(self, **kwargs):
        self.calls.append(("disable", kwargs)); return {}

    def admin_enable_user(self, **kwargs):
        self.calls.append(("enable", kwargs)); return {}

    def admin_user_global_sign_out(self, **kwargs):
        self.calls.append(("logout", kwargs)); return {}


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.table, self.provider = AuthTable(), Cognito()
        for context in [patch.dict(os.environ, {"AUTH_ENFORCED": "true", "API_TOKEN": TOKEN,
                "COGNITO_ISSUER": ISSUER, "COGNITO_CLIENT_ID": "browser-client", "COGNITO_USER_POOL_ID": "test-pool",
                "COGNITO_DOMAIN": "https://synthetic.auth.example", "HTTP_API_ID": "test-api",
                "FRONTEND_ORIGIN": "https://dkbznu9f5nh9n.cloudfront.net"}), patch.object(app, "_table", self.table),
                patch.object(app, "now_utc", return_value=NOW), patch.object(accounts, "_cognito", self.provider)]:
            context.start(); self.addCleanup(context.stop)
        self.addCleanup(lambda: auth.principal.set(None))
        control = store.make(app, "auth-control", "administrators", {"active_administrators": 1, "http_bootstrap_closed": True})
        self.table.items[control["PK"], control["SK"]] = control
        key = store.make(app, "auth-control", staff_reads.CURSOR_KEY_ID,
                         {"purpose": "staff-workflow-cursors-v1", "encryption_key": base64.urlsafe_b64encode(bytes(range(32))).decode()})
        self.table.items[key["PK"], key["SK"]] = key
        self.seed("admin", "Administrator")
        self.seed("operator", "Operator")
        self.seed("viewer", "Viewer")

    def seed(self, staff_id, role, status="active"):
        staff = store.make(app, "staff", staff_id, {"staff_id": staff_id, "name": staff_id,
                          "email": staff_id + "@example.com", "role": role})
        account = store.make(app, "account", staff_id, {"staff_id": staff_id, "role": role, "status": status,
                "issuer": ISSUER, "subject": "sub-" + staff_id, "username": "user-" + staff_id,
                "provider_status": "CONFIRMED", "revoked_before": -1})
        identity = store.make(app, "identity", auth.identity_key(ISSUER, "sub-" + staff_id), {"staff_id": staff_id})
        for item in (staff, account, identity):
            self.table.items[item["PK"], item["SK"]] = item
        return account

    def event(self, method, path, data=None, user="admin", claims=None, query=None, version=None):
        now = int(NOW.timestamp())
        jwt = {"iss": ISSUER, "client_id": "browser-client", "token_use": "access", "sub": "sub-" + user,
               "exp": str(now + 900), "iat": str(now - 10)}
        jwt.update(claims or {})
        headers = {"authorization": "Bearer synthetic-not-a-real-JWT", "content-type": "application/json"}
        if version is not None:
            headers["if-match"] = str(version)
        return {"rawPath": path, "headers": headers, "body": json.dumps(data) if data is not None else "",
                "queryStringParameters": query, "requestContext": {"http": {"method": method}, "apiId": "test-api",
                 "routeKey": "ANY /staff-api/{proxy+}", "authorizer": {"jwt": {"claims": jwt, "scopes": [auth.SCOPE]}}}}

    def request(self, *args, **kwargs):
        result = app.lambda_handler(self.event(*args, **kwargs), None)
        return result["statusCode"], json.loads(result["body"])

    def test_session_uses_immutable_subject_and_current_application_role(self):
        status, body = self.request("GET", "/staff-api/session", user="operator", claims={"cognito:groups": "Administrator"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["user"]["role"], "Operator")
        self.assertFalse(body["user"]["permissions"]["manage_accounts"])
        self.assertEqual(body["user"]["staff_id"], "operator")

    def test_claim_purpose_issuer_client_expiry_scope_and_gateway_binding(self):
        cases = [{"token_use": "id"}, {"iss": "https://other"}, {"client_id": "wrong"},
                 {"exp": str(int(NOW.timestamp()))}, {"iat": "not-a-number"}, {"sub": "unlinked"}]
        for claims in cases:
            with self.subTest(claims=claims):
                status, _ = self.request("GET", "/staff-api/session", claims=claims)
                self.assertIn(status, (401, 403))
        for mutate in [lambda e: e["requestContext"].pop("authorizer"), lambda e: e["requestContext"].update(apiId="other"),
                       lambda e: e["requestContext"].update(routeKey="$default"),
                       lambda e: e["requestContext"]["authorizer"]["jwt"].update(scopes=[])]:
            event = self.event("GET", "/staff-api/session"); mutate(event)
            self.assertEqual(app.lambda_handler(event, None)["statusCode"], 401)

    def test_missing_jwt_cannot_be_replaced_by_shared_token(self):
        event = self.event("GET", "/staff-api/session")
        event["requestContext"].pop("authorizer"); event["headers"]["authorization"] = "Bearer " + TOKEN
        self.assertEqual(app.lambda_handler(event, None)["statusCode"], 401)
        for method, path in [("GET", "/api/staff"), ("POST", "/api/exhibit-profiles"), ("POST", "/api/commands"),
                             ("POST", "/api/media/upload-requests"), ("POST", "/api/report-snapshots")]:
            event = self.event(method, path, {}); event["headers"]["authorization"] = "Bearer " + TOKEN
            self.assertEqual(app.lambda_handler(event, None)["statusCode"], 403, path)

    def test_permission_matrix_direct_api_denials(self):
        admin_only = [("GET", "/staff-api/staff"), ("GET", "/staff-api/access-attempts"),
                      ("GET", "/staff-api/accounts"), ("POST", "/staff-api/staff"),
                      ("PATCH", "/staff-api/accounts/admin"), ("POST", "/staff-api/accounts/invitations")]
        operator_writes = [("POST", "/staff-api/exhibit-profiles"), ("PATCH", "/staff-api/exhibit-profiles/ex-1"),
                           ("PUT", "/staff-api/label-drafts/ex-1"), ("POST", "/staff-api/commands"),
                           ("POST", "/staff-api/media/upload-requests"), ("POST", "/staff-api/report-snapshots")]
        for user in ("viewer", "operator"):
            for method, path in admin_only:
                self.assertEqual(self.request(method, path, {}, user=user)[0], 403, (user, path))
        for method, path in operator_writes:
            self.assertEqual(self.request(method, path, {}, user="viewer")[0], 403, path)
        for user in ("admin", "operator", "viewer"):
            for path in ["/staff-api/readings", "/staff-api/device-status", "/staff-api/tag-scans", "/staff-api/coverage",
                         "/staff-api/commands/x/acknowledgements", "/staff-api/commands/x/recovery-acknowledgements"]:
                self.assertEqual(self.request("POST", path, {}, user=user)[0], 403, (user, path))

    def test_disabled_and_demoted_accounts_reject_older_tokens_immediately(self):
        row = self.table.items["WF#account", "operator"]
        row["data"]["role"] = "Viewer"; row["version"] += 1
        self.assertEqual(self.request("POST", "/staff-api/commands", {}, user="operator")[0], 403)
        row["data"]["status"] = "disabled"
        self.assertEqual(self.request("GET", "/staff-api/session", user="operator")[0], 403)

    def media_storage(self):
        s3 = S3()
        self.enterContext(patch.dict(os.environ, {"ASSET_BUCKET": "private-test-bucket"}))
        self.enterContext(patch.object(media_reports, "_s3", s3))
        return s3

    def test_staff_media_round_trip_and_independent_versioned_associations(self):
        s3 = self.media_storage()
        # Real 1x1 images. Only transport and storage are simulated.
        fixtures = {
            "png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mNgAAAAAgAB5Sfe/AAAAABJRU5ErkJggg==",
            "jpeg": ("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n"
                     "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACP/EABQQ"
                     "AQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AP7//2Q=="),
            "webp": "UklGRhoAAABXRUJQVlA4TA4AAAAvAAAAAAcQEf0PRET/Aw==",
        }
        profile = {"exhibit_id": "fixture-image-exhibit", "name": "Synthetic exhibit", "description": "Test",
                   "tag_id": "EX-IMAGE-001", "temperature_min_c": 18, "temperature_max_c": 28,
                   "brightness_min_percent": 10, "brightness_max_percent": 90, "warning_seconds": 10}
        profiles = "/staff-api/exhibit-profiles"
        profile_path = profiles + "/fixture-image-exhibit"
        label_path = "/staff-api/label-drafts/fixture-image-exhibit"
        label = {"title": "Synthetic label", "collection": "", "description": "Test", "image_media_id": None}
        status, response = self.request("POST", profiles, profile, user="operator")
        self.assertEqual(status, 201, response)
        self.assertIsNone(response["item"]["image_media_id"])
        self.assertEqual(self.request("PUT", label_path, label, user="admin", version=0)[0], 201)

        profile_version = 1
        expected_profile, expected_draft = None, None
        for extension, encoded in fixtures.items():
            with self.subTest(extension=extension):
                raw = base64.b64decode(encoded)
                # GET must still resolve an ID that matches the POST path segment.
                image_id = "upload-requests" if extension == "png" else "fixture-image-" + extension
                media_path = "/staff-api/media/" + image_id
                upload = {"media_id": image_id, "content_type": "image/" + extension, "size_bytes": len(raw)}
                user = "admin" if extension == "png" else "operator"
                status, response = self.request("POST", "/staff-api/media/upload-requests", upload, user=user)
                self.assertEqual(status, 201, response)
                self.assertEqual(response["item"]["status"], "pending")
                status, pending = self.request("GET", media_path, user="viewer")
                self.assertEqual(status, 200, pending)
                self.assertEqual(pending["item"]["status"], "pending")
                self.assertNotIn("download", pending)
                self.assertEqual(self.request("PATCH", profile_path, {"image_media_id": image_id},
                                              user=user, version=profile_version)[1]["error"]["code"], "media_not_ready")
                if extension == "jpeg":
                    status, pending = self.request("PUT", label_path, {**label, "image_media_id": image_id},
                                                   user=user, version=1)
                    self.assertEqual((status, pending["error"]["code"]), (409, "media_not_ready"))
                self.assertEqual(self.request("POST", media_path + "/complete", {}, user=user)[1]["error"]["code"],
                                 "upload_incomplete")
                staging_key = response["upload"]["fields"]["key"]
                if extension == "webp":
                    s3.objects[staging_key] = {"body": b"x" * len(raw), "type": upload["content_type"]}
                    status, invalid = self.request("POST", media_path + "/complete", {}, user=user)
                    self.assertEqual((status, invalid["error"]["code"]), (400, "invalid_asset"))
                    self.assertEqual(self.request("GET", profile_path)[1]["item"]["image_media_id"], expected_profile)
                s3.objects[staging_key] = {"body": raw, "type": upload["content_type"]}
                status, ready = self.request("POST", media_path + "/complete", {}, user=user)
                self.assertEqual(status, 200, ready)
                self.assertEqual(ready["item"]["sha256"], hashlib.sha256(raw).hexdigest())
                status, duplicate = self.request("POST", "/staff-api/media/upload-requests", upload, user=user)
                self.assertEqual((status, duplicate), (200, {"status": "duplicate", "item": ready["item"]}))
                if extension == "jpeg":
                    self.assertEqual(self.request("PUT", label_path, {**label, "image_media_id": image_id},
                                                  user=user, version=1)[0], 200)
                    expected_draft = image_id
                else:
                    if profile_version > 1:
                        status, conflict = self.request("PATCH", profile_path, {"image_media_id": image_id},
                                                        user=user, version=1)
                        self.assertEqual((status, conflict["error"]["code"]), (409, "version_conflict"))
                        self.assertEqual(self.request("GET", profile_path)[1]["item"]["image_media_id"], expected_profile)
                    status, associated = self.request("PATCH", profile_path, {"image_media_id": image_id},
                                                      user=user, version=profile_version)
                    self.assertEqual(status, 200, associated)
                    profile_version = associated["item"]["version"]
                    expected_profile = image_id
                self.assertEqual(self.request("GET", profile_path)[1]["item"]["image_media_id"], expected_profile)
                self.assertEqual(self.request("GET", label_path)[1]["item"]["content"]["image_media_id"], expected_draft)
                status, readback = self.request("GET", media_path, user="viewer")
                self.assertEqual(status, 200, readback)
                self.assertEqual(readback["item"], ready["item"])
                self.assertEqual(readback["download"]["expires_in_seconds"], 300)
                final_key = s3.calls[-1][1]["Params"]["Key"]
                self.assertEqual(s3.objects[final_key], {"body": raw, "type": upload["content_type"]})

        status, edited = self.request("PATCH", profile_path, {"name": "Updated text"}, version=profile_version)
        self.assertEqual(status, 200, edited)
        self.assertEqual(edited["item"]["image_media_id"], expected_profile)
        status, cleared = self.request("PATCH", profile_path, {"image_media_id": None}, version=edited["item"]["version"])
        self.assertEqual(status, 200, cleared)
        self.assertIsNone(cleared["item"]["image_media_id"])
        self.assertEqual(self.request("PUT", label_path, {key: value for key, value in label.items()
                         if key != "image_media_id"}, version=2)[0], 400)
        self.assertEqual(self.request("PUT", label_path, {**label, "image_media_id": "unknown"}, version=2)[0], 404)
        self.assertEqual(self.request("GET", label_path)[1]["item"]["content"]["image_media_id"], expected_draft)
        self.assertEqual(self.request("PUT", label_path, label, version=2)[0], 200)
        self.assertIsNone(self.request("GET", label_path)[1]["item"]["content"]["image_media_id"])
        self.assertFalse(any(row["PK"] in ("WF#command", "WF#showcase", "WF#acknowledgement")
                             for row in self.table.items.values()))
        self.assertEqual(sum(key.startswith("media/final/") for key in s3.objects), 3)

    def test_staff_media_upload_request_race_uses_committed_metadata(self):
        self.media_storage()
        path = "/staff-api/media/upload-requests"
        for media_id, competing_size, expected in (("same", 50, 200), ("different", 51, 409)):
            with self.subTest(media_id=media_id):
                request = {"media_id": media_id, "content_type": "image/png", "size_bytes": 50}
                committed = store.make(app, "media", media_id, {**request, "size_bytes": competing_size,
                                       "status": "pending", "staging_key": "media/staging/" + media_id + "/committed"})
                self.table.before_transaction = lambda items: items.update({("WF#media", media_id): committed})
                status, response = self.request("POST", path, request, user="operator")
                self.assertEqual(status, expected, response)
                if expected == 200:
                    self.assertEqual(response["status"], "duplicate")
                    self.assertEqual(response["upload"]["fields"]["key"], committed["data"]["staging_key"])
                else:
                    self.assertEqual(response["error"]["code"], "id_conflict")

    def test_staff_media_current_permissions_audit_and_safe_retry(self):
        s3 = self.media_storage()
        upload = "/staff-api/media/upload-requests"
        metadata = {"media_id": "fixture-permissions", "content_type": "image/png", "size_bytes": 68}
        path = "/staff-api/media/fixture-permissions"
        complete = path + "/complete"
        self.assertEqual(self.request("POST", upload, {**metadata, "actor": "forged"}, user="operator")[0], 403)
        self.assertEqual(self.request("POST", upload, metadata, user="viewer")[0], 403)
        with patch.object(s3, "generate_presigned_post", side_effect=RuntimeError("simulated unavailable S3")), \
             self.assertLogs(app.LOG, "ERROR"):
            status, result = self.request("POST", upload, metadata, user="operator")
        self.assertEqual(status, 503)
        self.assertIn("same media_id", result["error"]["message"])
        status, retried = self.request("POST", upload, metadata, user="operator")
        self.assertEqual(status, 200, retried)
        self.assertEqual(retried["status"], "duplicate")
        audits = [row["data"] for row in self.table.items.values() if row["PK"] == "WF#staff-audit"]
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["actor"], {"staff_id": "operator", "role": "Operator",
                                             "issuer": ISSUER, "subject": "sub-operator"})
        self.assertEqual(self.request("POST", complete, {}, user="viewer")[0], 403)
        self.assertEqual(self.request("GET", path, user="viewer")[0], 200)

        account = self.table.items["WF#account", "operator"]
        account["data"]["role"] = "Viewer"
        self.assertEqual(self.request("POST", complete, {}, user="operator")[0], 403)
        account["data"]["status"] = "disabled"
        self.assertEqual(self.request("GET", path, user="operator")[1]["error"]["code"], "account_unavailable")
        self.assertEqual(self.request("POST", complete, {}, claims={"exp": str(int(NOW.timestamp()))})[0], 401)
        self.assertEqual(self.request("POST", complete, {}, claims={"token_use": "id"})[0], 401)
        event = self.event("POST", complete, {})
        event["requestContext"].pop("authorizer")
        for header in ("", "Bearer invalid", "Bearer " + TOKEN, "Bearer " + HARDWARE_TOKEN):
            event["headers"]["authorization"] = header
            self.assertEqual(app.lambda_handler(event, None)["statusCode"], 401)
        event["rawPath"] = complete.replace("/staff-api/", "/api/")
        event["headers"]["authorization"] = "Bearer " + TOKEN
        self.assertEqual(app.lambda_handler(event, None)["statusCode"], 403)

    def test_logout_invalidates_existing_access_tokens_but_preserves_cards(self):
        before_staff = copy.deepcopy(self.table.items["WF#staff", "operator"])
        status, body = self.request("POST", "/staff-api/session/logout", {}, user="operator")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["application_sessions_revoked"])
        self.assertEqual(self.request("GET", "/staff-api/session", user="operator")[0], 401)
        self.assertEqual(self.table.items["WF#staff", "operator"], before_staff)

    def test_last_admin_and_concurrent_role_change_are_guarded(self):
        self.assertEqual(self.request("PATCH", "/staff-api/accounts/admin", {"role": "Viewer"}, version=1)[0], 409)
        second = self.seed("second", "Administrator")
        self.table.items["WF#auth-control", "administrators"]["data"]["active_administrators"] = 2
        caller = auth.human(app, self.event("GET", "/staff-api/session"))
        control = self.table.items["WF#auth-control", "administrators"]
        def concurrent(items):
            items[control["PK"], control["SK"]]["version"] += 1
            items[control["PK"], control["SK"]]["data"]["active_administrators"] = 1
        self.table.before_transaction = concurrent
        with self.assertRaises(app.APIError) as result:
            accounts.change(app, second, {**second["data"], "role": "Viewer"}, caller)
        self.assertEqual(result.exception.code, "version_conflict")
        self.assertEqual(self.table.items["WF#account", "second"]["data"]["role"], "Administrator")

    def test_invitation_retry_after_provider_success_creates_only_one_subject(self):
        staff = store.make(app, "staff", "new-staff", {"staff_id": "new-staff", "name": "New", "email": "new@example.com", "role": "Viewer"})
        self.table.items[staff["PK"], staff["SK"]] = staff
        body = {"invitation_id": "invite-one", "staff_id": "new-staff", "role": "Operator"}
        with patch.object(accounts, "change", side_effect=RuntimeError("simulated DB failure after provider creation")):
            self.assertEqual(self.request("POST", "/staff-api/accounts/invitations", body)[0], 503)
        self.assertEqual(len(self.provider.users), 1)
        status, result = self.request("POST", "/staff-api/accounts/invitations", body)
        self.assertEqual(status, 201, result)
        self.assertEqual(result["item"]["status"], "invited")
        self.assertEqual(self.request("POST", "/staff-api/accounts/invitations", body)[0], 200)
        self.assertEqual(len(self.provider.users), 1)
        creates = [kwargs for action, kwargs in self.provider.calls if action == "create"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["MessageAction"], "SUPPRESS")
        self.assertNotIn("TemporaryPassword", creates[0])
        self.assertNotIn("TemporaryPassword", json.dumps(result))

    def test_forged_actor_is_rejected_and_mutation_audit_uses_subject(self):
        status, _ = self.request("POST", "/staff-api/showcases", {"showcase_id": "auth-case", "name": "Test", "device_id": "v04-auth"})
        self.assertEqual(status, 201)
        audits = [row for row in self.table.items.values() if row["PK"] == "WF#staff-audit"]
        self.assertTrue(audits)
        self.assertEqual(audits[-1]["data"]["actor"]["subject"], "sub-admin")
        status, body = self.request("POST", "/staff-api/commands", {"actor": "forged", "command_id": "x"})
        self.assertEqual(status, 403, body)

    def test_demo_scope_and_source_cannot_upload_or_read_real_data(self):
        for device, source in [("real-device", "demo"), ("v04-demo", "device")]:
            event = self.event("POST", "/api/device-status", {"device_id": device, "source": source})
            event["headers"]["authorization"] = "Bearer " + TOKEN
            self.assertEqual(app.lambda_handler(event, None)["statusCode"], 403)
        event = self.event("GET", "/api/reports/event", query={"device_id": "real-device"})
        event["headers"]["authorization"] = "Bearer " + TOKEN
        self.assertEqual(app.lambda_handler(event, None)["statusCode"], 403)

    def hardware_credential(self):
        key = ("WF#hardware-credential", hashlib.sha256(HARDWARE_TOKEN.encode()).hexdigest())
        self.table.items[key] = {"PK": key[0], "SK": key[1],
                                 "data": {"device_id": HARDWARE_DEVICE, "active": True}}
        return key

    def producer_request(self, method, path, data=None, token=HARDWARE_TOKEN):
        event = {"rawPath": path, "requestContext": {"http": {"method": method}, "routeKey": "$default"},
                 "headers": {"authorization": "Bearer " + token, "content-type": "application/json"},
                 "body": json.dumps(data) if data is not None else ""}
        result = app.lambda_handler(event, None)
        self.assertNotIn(HARDWARE_TOKEN, result["body"])
        return result["statusCode"], json.loads(result["body"])

    def test_hardware_uploads_require_existing_showcase_and_exhibit(self):
        self.hardware_credential()
        reading = {"reading_id": "fixture-reading", "device_id": HARDWARE_DEVICE,
                   "exhibit_id": "demo-artifact-01", "recorded_at": app.timestamp(NOW),
                   "temperature_c": 22, "brightness_raw": 1800, "occupied": False,
                   "distance_cm": None, "source": "device"}
        status = {"status_id": "fixture-status", "device_id": HARDWARE_DEVICE, "recorded_at": app.timestamp(NOW),
                  "source": "device", "temperature_c": None, "brightness_percent": None, "occupied": False,
                  "distance_cm": None, "lighting_mode": None, "lighting_output": None, "latch": None,
                  "local_control": None}
        before = copy.deepcopy(self.table.items)
        self.assertEqual(self.producer_request("POST", "/api/device-status", status)[0], 404)
        self.assertEqual(self.producer_request("POST", "/api/readings", reading)[0], 404)
        self.assertEqual(self.table.items, before)

        showcase = {"showcase_id": "fixture-showcase", "name": "Fixture showcase", "device_id": HARDWARE_DEVICE}
        self.assertEqual(self.request("POST", "/staff-api/showcases", showcase)[0], 201)
        exhibit = {"exhibit_id": "demo-artifact-01", "name": "Fixture exhibit", "description": "",
                   "tag_id": "demo-artifact-tag", "temperature_min_c": 18, "temperature_max_c": 28,
                   "brightness_min_raw": 600, "brightness_max_raw": 3000}
        self.assertEqual(self.producer_request("POST", "/api/exhibits", exhibit, token=TOKEN)[0], 201)

        for path, payload in (("/api/readings", reading), ("/api/device-status", status)):
            with self.subTest(path=path):
                code, created = self.producer_request("POST", path, payload)
                self.assertEqual(code, 201, created)
                self.assertEqual(created["item"]["device_id"], HARDWARE_DEVICE)
                self.assertEqual(created["item"]["source"], "device")
                stored = copy.deepcopy(self.table.items)
                code, duplicate = self.producer_request("POST", path, payload)
                self.assertEqual(code, 200, duplicate)
                self.assertEqual(duplicate["item"], created["item"])
                self.assertEqual(self.producer_request("POST", path, {**payload, "occupied": True})[0], 409)
                self.assertEqual(self.table.items, stored)

    def test_hardware_token_is_write_only_for_its_two_operations_and_payload_scope(self):
        self.hardware_credential()
        payload = {"source": "device", "device_id": HARDWARE_DEVICE}
        before = copy.deepcopy(self.table.items)
        for method, path in (("GET", "/api/readings"), ("GET", "/api/readings/id"), ("GET", "/api/device-status"),
                             ("GET", "/api/device-status/id"), ("POST", "/api/events"), ("POST", "/api/exhibits"),
                             ("PATCH", "/api/device-status"), ("POST", "/api/readings/"), ("GET", "/api/schema")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.producer_request(method, path, payload)[0], 403)
        self.assertEqual(self.producer_request("POST", "/staff-api/readings", payload)[0], 401)
        for path in ("/api/readings", "/api/device-status"):
            with self.subTest(path=path):
                for invalid in ({**payload, "source": "demo"}, {**payload, "device_id": "other-device"},
                                {"source": "device"}, {"device_id": HARDWARE_DEVICE}):
                    self.assertEqual(self.producer_request("POST", path, invalid)[0], 403)
                self.assertEqual(self.producer_request("POST", path, payload, token=TOKEN)[0], 403)
                self.assertEqual(self.request("POST", "/staff-api" + path.removeprefix("/api"), payload)[0], 403)
        self.assertEqual(self.table.items, before)

    def test_hardware_auth_reads_exact_digest_strongly_and_rejects_revoked_or_invalid_rows(self):
        key = self.hardware_credential()
        self.table.calls.clear()
        # A permitted route reaches payload validation without writing telemetry.
        self.assertEqual(self.producer_request("POST", "/api/readings")[0], 400)
        self.assertEqual(self.table.calls, [("get_item", {"Key": {"PK": key[0], "SK": key[1]}, "ConsistentRead": True})])
        for invalid in ({"device_id": HARDWARE_DEVICE, "active": False}, {"device_id": HARDWARE_DEVICE, "active": 1},
                        {"device_id": "demo-device", "active": True}, {"device_id": "bad/device", "active": True},
                        {"active": True}):
            with self.subTest(credential=invalid):
                self.table.items[key]["data"] = invalid
                self.assertEqual(self.producer_request("POST", "/api/readings")[0], 401)
        self.table.items.pop(key)
        self.assertEqual(self.producer_request("POST", "/api/readings")[0], 401)
        self.table.calls.clear()
        self.assertEqual(self.producer_request("POST", "/api/readings", token=HARDWARE_TOKEN + "=")[0], 401)
        self.assertEqual(self.table.calls, [])

    def test_demo_producer_exact_command_read_is_scoped_for_fenced_execution(self):
        for identity, device in (("demo-command", "v04-demo-device"), ("real-command", "real-device")):
            item = store.make(app, "command", identity, {"command_id": identity, "device_id": device,
                              "showcase_id": "case", "type": "assignment", "status": "pending"})
            self.table.items[item["PK"], item["SK"]] = item
        for path, expected in (("/api/commands/demo-command", 200), ("/api/commands/real-command", 403),
                               ("/api/commands/demo-command/nested", 403)):
            event = self.event("GET", path)
            event["headers"]["authorization"] = "Bearer " + TOKEN
            result = app.lambda_handler(event, None)
            self.assertEqual(result["statusCode"], expected, result)
            if expected == 200:
                self.assertEqual(json.loads(result["body"])["item"]["device_id"], "v04-demo-device")
        event = self.event("POST", "/api/commands", {})
        event["headers"]["authorization"] = "Bearer " + TOKEN
        self.assertEqual(app.lambda_handler(event, None)["statusCode"], 403)

    def test_provider_configuration_has_no_secret_signup_or_implicit_flow(self):
        template = json.loads((Path(__file__).resolve().parents[1] / "infra/template.json").read_text())
        client = template["Resources"]["StaffBrowserClient"]["Properties"]
        self.assertFalse(client["GenerateSecret"])
        self.assertEqual(client["AllowedOAuthFlows"], ["code"])
        self.assertEqual(client["AccessTokenValidity"], 15)
        self.assertEqual(client["AllowedOAuthScopes"], [auth.SCOPE])
        self.assertTrue(template["Resources"]["StaffUserPool"]["Properties"]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"])
        # Deployment rejected spaces in this actual HTTP API authorizer name;
        # retain AWS's accepted identifier character set in the template.
        self.assertRegex(template["Resources"]["StaffJwtAuthorizer"]["Properties"]["Name"], r"^[A-Za-z0-9._-]+$")
        self.assertNotIn("password", json.dumps(auth.config(app)).lower())

    def workflow_record(self, kind, record_id, data, seconds=0):
        item = store.make(app, kind, record_id, data)
        at = app.timestamp(NOW + timedelta(seconds=seconds))
        item.update(created_at=at, updated_at=at)
        for row in [item, *(action["Put"]["Item"] for action in store.index_actions(app, item))]:
            self.table.items[row["PK"], row["SK"]] = row
        return item

    def command_record(self, command_id, kind="lighting", seconds=0):
        return self.workflow_record("command", command_id,
            {"command_id": command_id, "showcase_id": "case", "device_id": "demo-device", "type": kind,
             "status": "recovered", "payload": {"mode": "Auto", "staff_id": "PRIVATE-STAFF"},
             "actor": "PRIVATE-STAFF:PRIVATE-SUBJECT", "request": {"actor": "PRIVATE-STAFF"},
             "lock_id": "PRIVATE-STAFF", "failure_reason": "PRIVATE-STAFF",
             "recovery": {"request": {"recovery_id": "private", "reason": "PRIVATE-STAFF", "actor": "PRIVATE-STAFF"},
                          "requested_at": app.timestamp(NOW), "fence_generation": 2, "acknowledgement_id": None,
                          "outcome": "not_applied", "observed_status_id": None, "staff_id": "PRIVATE-STAFF"},
             "staff_id": "PRIVATE-STAFF"}, seconds)

    def all_pages(self, path, user, query):
        items, pages = [], []
        for _ in range(20):
            status, page = self.request("GET", path, user=user, query=query)
            self.assertEqual(status, 200, page)
            items.extend(page["items"]); pages.append(page)
            self.assertNotIn("count", page)
            self.assertNotIn("scanned_count", page)
            if page["next_cursor"] is None:
                return items, pages
            query = {**query, "cursor": page["next_cursor"]}
        self.fail("Pagination did not terminate within the isolated fixture size")

    def test_operator_exhibit_scan_read_policy_filters_list_and_exact_without_trusting_query(self):
        for scan_id, scan_type, tag, seconds in (("exhibit-scan", "exhibit", "EX-1", -2),
                ("PRIVATE-CARD-SCAN", "staff_card", "CARD-PRIVATE", -1), ("future-scan", "future", "PRIVATE", 0)):
            self.workflow_record("scan", scan_id, {"scan_id": scan_id, "device_id": "demo-device", "source": "demo",
                "recorded_at": app.timestamp(NOW), "scan_type": scan_type, "tag_id": tag, "staff_id": "PRIVATE-STAFF"}, seconds)
        query = {"device_id": "demo-device", "limit": "1"}
        rows, pages = self.all_pages("/staff-api/tag-scans", "operator", query)
        self.assertEqual([row["scan_id"] for row in rows], ["exhibit-scan"])
        self.assertEqual(pages[0]["items"], [])
        self.assertIsNotNone(pages[0]["next_cursor"])
        self.assertNotIn(b"PRIVATE", base64.urlsafe_b64decode(pages[0]["next_cursor"]))
        self.assertNotIn("PRIVATE", json.dumps(rows))
        self.assertEqual(len(self.all_pages("/staff-api/tag-scans", "admin", query)[0]), 3)
        self.assertEqual(self.request("GET", "/staff-api/tag-scans/exhibit-scan", user="operator")[0], 200)
        for scan_id in ("PRIVATE-CARD-SCAN", "future-scan"):
            self.assertEqual(self.request("GET", "/staff-api/tag-scans/" + scan_id, user="operator")[0], 403)
            self.assertEqual(self.request("GET", "/staff-api/tag-scans/" + scan_id)[0], 200)
        for path in ("/staff-api/tag-scans", "/staff-api/tag-scans/exhibit-scan"):
            self.assertEqual(self.request("GET", path, user="viewer", query=query if path.endswith("scans") else None)[0], 403)
        for extra, expected in (({"scan_type": "staff_card"}, 403), ({"scan_type": "exhibit"}, 200),
                ({"scan_type": "future"}, 400), ({"scan_type": ""}, 400), ({"staff_id": "anything"}, 400)):
            self.assertEqual(self.request("GET", "/staff-api/tag-scans", user="operator", query={**query, **extra})[0], expected)
        self.assertEqual(self.request("GET", "/staff-api/tag-scans", user="operator")[0], 400)
        self.assertFalse(any(operation in ("put_item", "transact_write_items", "scan") for operation, _ in self.table.calls))

    def test_non_admin_command_reads_allow_only_known_exhibition_types_and_project_nested_data(self):
        self.command_record("safe-light", seconds=-2)
        self.command_record("PRIVATE-CARD", "card", -1)
        self.command_record("PRIVATE-FUTURE", "future", 0)
        query = {"device_id": "demo-device", "limit": "1"}
        for user in ("operator", "viewer"):
            rows, pages = self.all_pages("/staff-api/commands", user, query)
            self.assertEqual([row["command_id"] for row in rows], ["safe-light"])
            self.assertEqual(pages[0]["items"], [])
            self.assertNotIn("PRIVATE", json.dumps(rows))
            self.assertEqual(rows[0]["payload"], {"mode": "Auto"})
            self.assertNotIn("request", rows[0]["recovery"])
            for command_id in ("PRIVATE-CARD", "PRIVATE-FUTURE"):
                self.assertEqual(self.request("GET", "/staff-api/commands/" + command_id, user=user)[0], 403)
            status, exact = self.request("GET", "/staff-api/commands/safe-light", user=user)
            self.assertEqual(status, 200, exact)
            self.assertEqual(exact["item"], rows[0])
            self.assertEqual(self.request("GET", "/staff-api/commands", user=user, query={**query, "type": "card"})[0], 403)
            self.assertEqual(self.request("POST", "/staff-api/commands/PRIVATE-CARD/recovery-requests", {}, user=user)[0], 403)
            self.assertEqual(self.request("POST", "/staff-api/commands", {"type": "card"}, user=user)[0], 403)
        self.assertIn("PRIVATE", json.dumps(self.request("GET", "/staff-api/commands/safe-light")[1]))
        self.assertEqual(len(self.all_pages("/staff-api/commands", "admin", query)[0]), 3)

    def test_operational_activity_feed_filters_type_link_and_explicit_fields_before_response(self):
        self.command_record("safe-light", seconds=-5)
        self.command_record("PRIVATE-CARD", "card", -4)
        for activity_id, kind, status, command, seconds in (
                ("safe-event", "lighting", "acknowledged", "safe-light", -3),
                ("PRIVATE-MISLABELED", "lighting", "acknowledged", "PRIVATE-CARD", -2),
                ("PRIVATE-STATUS", "lighting", "PRIVATE-status", "safe-light", -1),
                ("PRIVATE-ACCOUNT", "staff_profile", "updated", None, 0)):
            self.workflow_record("activity", activity_id, {"activity_id": activity_id, "type": kind, "status": status,
                "device_id": "demo-device", "command_id": command, "actor": "PRIVATE-STAFF", "identity": "PRIVATE-SUBJECT",
                "message": "PRIVATE-CARD", "payload": {"card_id": "PRIVATE-CARD"}}, seconds)
        query = {"device_id": "demo-device", "limit": "1"}
        for user in ("operator", "viewer"):
            rows, pages = self.all_pages("/staff-api/workflow-activity", user, query)
            self.assertEqual([row["activity_id"] for row in rows], ["safe-event"])
            self.assertEqual(pages[0]["items"], [])
            self.assertTrue(set(rows[0]) <= set(staff_reads.ACTIVITY_FIELDS))
            self.assertNotIn("PRIVATE", json.dumps(rows))
            self.assertEqual(self.request("GET", "/staff-api/workflow-activity/safe-event", user=user)[0], 200)
            for activity_id in ("PRIVATE-MISLABELED", "PRIVATE-STATUS", "PRIVATE-ACCOUNT"):
                self.assertEqual(self.request("GET", "/staff-api/workflow-activity/" + activity_id, user=user)[0], 403)
            self.assertEqual(self.request("GET", "/staff-api/workflow-activity", user=user, query={**query, "type": "card"})[0], 403)
            self.assertEqual(self.request("GET", "/staff-api/workflow-activity", user=user, query={**query, "type": "lighting"})[0], 200)
            self.assertEqual(self.request("GET", "/staff-api/workflow-activity", user=user, query={**query, "actor": "admin"})[0], 400)
        self.assertEqual(len(self.all_pages("/staff-api/workflow-activity", "admin", query)[0]), 4)

    def test_restricted_cursor_is_confidential_and_binds_identity_role_query_and_current_account(self):
        self.command_record("safe-light", seconds=-1)
        self.command_record("PRIVATE-CARD", "card", 0)
        query = {"device_id": "demo-device", "limit": "1"}
        first = self.request("GET", "/staff-api/commands", user="operator", query=query)[1]
        cursor = first["next_cursor"]
        raw = base64.urlsafe_b64decode(cursor)
        self.assertEqual(len(raw), 1052)
        for sentinel in (b"PRIVATE", b"WFIDX", b"SK", b"demo-device"):
            self.assertNotIn(sentinel, raw)
        self.assertEqual(self.request("GET", "/staff-api/commands", user="operator", query={**query, "cursor": cursor})[0], 200)
        self.assertEqual(self.request("GET", "/staff-api/commands", user="operator", query={**query, "cursor": cursor, "limit": "2"})[0], 200)
        for user in ("admin", "viewer"):
            self.assertEqual(self.request("GET", "/staff-api/commands", user=user, query={**query, "cursor": cursor})[0], 400)
        self.seed("other-operator", "Operator")
        self.assertEqual(self.request("GET", "/staff-api/commands", user="other-operator", query={**query, "cursor": cursor})[0], 400)
        for change in ({"device_id": "other-device"}, {"type": "lighting"}, {"start": "2026-10-26T00:00:00Z"}):
            self.assertEqual(self.request("GET", "/staff-api/commands", user="operator", query={**query, **change, "cursor": cursor})[0], 400)
        self.assertEqual(self.request("GET", "/staff-api/workflow-activity", user="operator", query={**query, "cursor": cursor})[0], 400)
        changed = bytearray(raw); changed[-1] ^= 1
        for bad in (base64.urlsafe_b64encode(changed).decode(), "!", "e30=", "A" * 2049):
            self.assertEqual(self.request("GET", "/staff-api/commands", user="operator", query={**query, "cursor": bad})[0], 400)
        with patch.object(app, "now_utc", return_value=NOW + timedelta(seconds=901)):
            self.assertEqual(self.request("GET", "/staff-api/commands", user="operator", query={**query, "cursor": cursor},
                claims={"exp": str(int(NOW.timestamp()) + 1800)})[0], 400)
        self.table.items["WF#account", "operator"]["version"] += 1
        self.assertEqual(self.request("GET", "/staff-api/commands", user="operator", query={**query, "cursor": cursor})[0], 400)
        self.table.items.pop(("WF#auth-control", staff_reads.CURSOR_KEY_ID))
        self.assertEqual(self.request("GET", "/staff-api/commands", user="operator", query=query)[0], 503)
        self.assertEqual(self.request("GET", "/staff-api/commands", query=query)[0], 200)

    def test_operator_create_and_recovery_return_projected_commands_without_losing_internal_audit(self):
        self.workflow_record("showcase", "case", {"showcase_id": "case", "device_id": "demo-device", "confirmed_exhibit_id": None})
        status, created = self.request("POST", "/staff-api/commands", {"command_id": "created-light", "showcase_id": "case",
            "type": "lighting", "payload": {"mode": "Auto"}}, user="operator")
        self.assertEqual(status, 201, created)
        self.assertNotIn("actor", created["item"])
        self.assertNotIn("request", created["item"])
        self.assertEqual(self.table.items["WF#command", "created-light"]["data"]["actor"], "operator:sub-operator")
        status, recovery = self.request("POST", "/staff-api/commands/created-light/recovery-requests",
            {"recovery_id": "recover-light", "reason": "PRIVATE-RECOVERY-REASON"}, user="operator")
        self.assertEqual(status, 201, recovery)
        self.assertNotIn("request", recovery["item"]["recovery"])
        self.assertNotIn("PRIVATE", json.dumps(recovery))
        self.assertEqual(self.table.items["WF#command", "created-light"]["data"]["recovery"]["request"]["reason"], "PRIVATE-RECOVERY-REASON")
        for user in ("admin", "operator", "viewer"):
            self.assertEqual(self.request("GET", "/staff-api/auth-control/" + staff_reads.CURSOR_KEY_ID, user=user)[0], 403)

    def test_operational_payloads_drop_unknown_nested_fields_and_match_published_role_variants(self):
        from jsonschema import Draft4Validator, FormatChecker, RefResolver
        from test_workflow_contract import json_schema
        spec = json.loads((Path(__file__).resolve().parents[1] / "backend/openapi.json").read_text())
        resolver = RefResolver.from_schema(json_schema(spec))
        def validate(path, value):
            definition = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            Draft4Validator(json_schema(definition), resolver=resolver, format_checker=FormatChecker()).validate(value)
        profile = {"exhibit_id": "demo-exhibit", "name": "Example", "description": "Example exhibit", "tag_id": "EX-1",
                   "temperature_min_c": 18, "temperature_max_c": 28, "brightness_min_percent": 10,
                   "brightness_max_percent": 80, "warning_seconds": 30, "staff_id": "PRIVATE-STAFF",
                   "version": 1, "created_at": app.timestamp(NOW), "updated_at": app.timestamp(NOW)}
        payloads = {"lighting": {"mode": "Auto", "card_id": "PRIVATE-CARD"},
                    "assignment": {"exhibit_id": "demo-exhibit", "profile": profile, "card_id": "PRIVATE-CARD"},
                    "label": {"exhibit_id": "demo-exhibit", "draft_version": 1, "assignment_command_id": "assignment-command",
                              "content": {"title": "Exhibit", "collection": "", "description": "Description", "image_media_id": None,
                                          "staff_id": "PRIVATE-STAFF"}, "card_id": "PRIVATE-CARD"}}
        for kind, payload in payloads.items():
            row = self.command_record(kind + "-command", kind)
            row["data"]["payload"] = payload
            self.workflow_record("activity", kind + "-event", {"activity_id": kind + "-event", "type": kind,
                "status": "acknowledged", "command_id": kind + "-command", "message": "PRIVATE", "identity": "PRIVATE"})
        for user in ("operator", "viewer"):
            for kind in payloads:
                status, body = self.request("GET", "/staff-api/commands/" + kind + "-command", user=user)
                self.assertEqual(status, 200, body)
                self.assertNotIn("PRIVATE", json.dumps(body))
                validate("/staff-api/commands/{command_id}", body)
            status, commands = self.request("GET", "/staff-api/commands", user=user)
            self.assertEqual(status, 200, commands)
            validate("/staff-api/commands", commands)
            status, activity = self.request("GET", "/staff-api/workflow-activity", user=user)
            self.assertEqual(status, 200, activity)
            validate("/staff-api/workflow-activity", activity)

    def test_actual_handler_auth_response_shapes_match_published_openapi(self):
        from jsonschema import Draft4Validator, FormatChecker, RefResolver
        from test_workflow_contract import json_schema
        spec = json.loads((Path(__file__).resolve().parents[1] / "backend/openapi.json").read_text())
        schema = json_schema(spec)
        resolver = RefResolver.from_schema(schema)
        def validate(path, method, status, value):
            definition = spec["paths"][path][method]["responses"][str(status)]["content"]["application/json"]["schema"]
            Draft4Validator(json_schema(definition), resolver=resolver, format_checker=FormatChecker()).validate(value)
        validate("/auth/config", "get", 200, auth.config(app))
        status, value = self.request("GET", "/staff-api/session")
        validate("/staff-api/session", "get", status, value)
        status, value = self.request("GET", "/staff-api/accounts/admin")
        validate("/staff-api/accounts/{staff_id}", "get", status, value)
        status, value = self.request("PATCH", "/staff-api/accounts/operator", {"role": "Viewer"}, version=1)
        validate("/staff-api/accounts/{staff_id}", "patch", status, value)
        status, value = self.request("POST", "/staff-api/session/logout", {}, user="viewer")
        validate("/staff-api/session/logout", "post", status, value)


if __name__ == "__main__":
    unittest.main()
