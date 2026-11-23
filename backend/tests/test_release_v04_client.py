"""Execute the live utility against real handler logic and local storage only.

This is developer-tool verification, not a provider/browser/S3 network claim.
"""
import importlib.util
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "demo"))
import app
import media_reports
import test_auth as auth_tests
from test_media_reports import S3

MODULE_PATH = Path(__file__).resolve().parents[1] / "demo/test_release_v04_live.py"


class ReleaseClientTests(unittest.TestCase):
    setUp = auth_tests.AuthenticationTests.setUp
    seed = auth_tests.AuthenticationTests.seed
    event = auth_tests.AuthenticationTests.event
    def test_release_utility_executes_actual_roles_workflow_analytics_and_media_logic(self):
        spec = importlib.util.spec_from_file_location("release_v04_live", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        owner, s3 = self, S3()
        class LocalClient:
            def __init__(self, role=None):
                self.role, self.responses = role, []
            def request(self, path, method="GET", payload=None, expected=(200,), headers=None, auth=True):
                parsed = urlsplit(path)
                event = owner.event(method, parsed.path, payload, user=self.role or "admin", query=dict(parse_qsl(parsed.query)))
                if self.role is None:
                    event["headers"]["authorization"] = "Bearer " + auth_tests.TOKEN
                    event["requestContext"].pop("authorizer")
                event["headers"].update({key.lower(): value for key, value in (headers or {}).items()})
                response = app.lambda_handler(event, None)
                raw = response["body"]
                value = json.loads(raw) if "application/json" in response["headers"].get("content-type", "") else raw
                if response["statusCode"] not in expected:
                    raise AssertionError((method, parsed.path, response["statusCode"], value))
                self.responses.append({"method": method, "path": parsed.path, "http_status": response["statusCode"], "response": module.redact(value)})
                return response["statusCode"], value
            def upload_png(self, form):
                s3.objects[form["fields"]["key"]] = {"body": module.PNG, "type": "image/png"}
            def download_png(self, download):
                return next(value["body"] for key, value in s3.objects.items() if key.startswith("media/final/"))
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return auth_tests.NOW
        with patch.object(module, "datetime", Clock), patch.object(media_reports, "_s3", s3), \
                patch.dict(__import__("os").environ, {"ASSET_BUCKET": "private-test-bucket"}):
            result = module.run(LocalClient("admin"), LocalClient(), "localv04", LocalClient("operator"), LocalClient("viewer"))
        self.assertGreaterEqual(result["check_count"], 20)
        self.assertTrue(all(row["passed"] for row in result["checks"]))
        self.assertEqual(result["report_totals"]["recorded_occupied_seconds"], 900)
        self.assertNotIn(auth_tests.TOKEN, json.dumps(result))
