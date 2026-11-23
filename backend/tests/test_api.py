"""Local contract and data-integrity tests, with an in-memory DynamoDB fake.

These tests do not establish AWS connectivity, hardware operation, or human
study results. Deployment verification must make real HTTPS calls and read
records back from DynamoDB.
"""

import base64
import copy
import hashlib
import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app

spec = importlib.util.spec_from_file_location("simulate_upload", ROOT / "demo" / "simulate_upload.py")
simulator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simulator)
TOKEN = "unit-test-token-" + "x" * 32


class ConditionalFailure(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self):
        self.items, self.calls = {}, []

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
            raise AssertionError("Write must be conditional to prevent overwrite")
        if key in self.items:
            raise ConditionalFailure()
        self.items[key] = copy.deepcopy(item)
        return {}

    def query(self, **kwargs):
        self.calls.append(("query", copy.deepcopy(kwargs)))
        if kwargs["IndexName"] != "device-readings-index" or kwargs["KeyConditionExpression"] != "GSI1PK = :device":
            raise AssertionError("Expected indexed device query")
        partition = kwargs["ExpressionAttributeValues"][":device"]
        items = sorted((copy.deepcopy(item) for item in self.items.values() if item.get("GSI1PK") == partition),
                       key=lambda item: item["GSI1SK"], reverse=not kwargs["ScanIndexForward"])
        result = {"Items": items[:kwargs["Limit"]]}
        if len(items) > kwargs["Limit"]:
            result["LastEvaluatedKey"] = {"PK": items[kwargs["Limit"] - 1]["PK"]}
        return result


class APITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"API_TOKEN": TOKEN, "AUTH_ENFORCED": "false"})
        self.env.start()
        self.table = FakeTable()
        self.table_patch = patch.object(app, "_table", self.table)
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)
        self.addCleanup(self.env.stop)
        self.exhibit = simulator.demo_exhibit("demo-artifact-01")
        self.reading = next(simulator.make_readings(1, "esp32-01", self.exhibit["exhibit_id"], "test-run"))

    def request(self, method, path, data=None, auth=True, query=None, raw_body=None, headers=None, encoded=False):
        request_headers = {"content-type": "application/json"}
        if auth:
            request_headers["authorization"] = "Bearer " + TOKEN
        request_headers.update(headers or {})
        event = {"version": "2.0", "requestContext": {"http": {"method": method}}, "rawPath": path,
                 "headers": request_headers, "queryStringParameters": query,
                 "body": raw_body if raw_body is not None else (json.dumps(data) if data is not None else ""),
                 "isBase64Encoded": encoded}
        result = app.lambda_handler(event, None)
        return result, json.loads(result["body"]) if "application/json" in result["headers"].get("content-type", "") else result["body"]

    def create_exhibit(self):
        result, _ = self.request("POST", "/api/exhibits", self.exhibit)
        self.assertEqual(result["statusCode"], 201)

    def test_public_health_and_ui_do_not_require_token_or_touch_storage(self):
        with patch.dict(os.environ, {"API_TOKEN": ""}):
            result, body = self.request("GET", "/api/health", auth=False)
            self.assertEqual(result["statusCode"], 200)
            self.assertIn("does not test database", body["check"])
            result, html = self.request("GET", "/", auth=False)
        self.assertEqual(result["statusCode"], 200)
        self.assertNotIn(TOKEN, html)
        self.assertNotIn("localStorage", html)
        self.assertIn("frame-ancestors 'none'", result["headers"]["content-security-policy"])
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn("'sha256-" + digest + "'", result["headers"]["content-security-policy"])
        self.assertEqual(self.table.calls, [])

    def test_public_swagger_ui_spec_and_pinned_assets_work_without_auth(self):
        for path in ("/", "/docs", "/docs/"):
            result, html = self.request("GET", path, auth=False)
            self.assertEqual(result["statusCode"], 200)
            self.assertIn("SwaggerUIBundle", html)
            self.assertIn("persistAuthorization: false", html)
            self.assertIn("validatorUrl: null", html)
            self.assertIn("withCredentials: false", html)
            self.assertIn("url: '/openapi.json'", html)
            self.assertNotIn(TOKEN, html)
            self.assertNotIn("https://", html)
        result, spec = self.request("GET", "/openapi.json", auth=False)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertEqual(spec["security"], [{"bearerAuth": []}])
        self.assertNotIn(TOKEN, json.dumps(spec))
        for path, (_, content_type) in app.SWAGGER_ASSETS.items():
            result, asset = self.request("GET", path, auth=False)
            self.assertEqual(result["statusCode"], 200)
            self.assertEqual(result["headers"]["content-type"], content_type)
            self.assertGreater(len(asset), 10000)
            self.assertIn("immutable", result["headers"]["cache-control"])
        self.assertEqual(self.table.calls, [])

    def test_static_asset_routes_use_exact_allowlist_not_arbitrary_files(self):
        for path in ("/assets/../app.py", "/assets/SWAGGER-VERSION.txt", "/assets/private.json", "/assets/swagger-ui-5.34.0-bundle.js/extra"):
            result, _ = self.request("GET", path)
            self.assertEqual(result["statusCode"], 404)
        self.assertEqual(self.table.calls, [])

    def test_cors_preflight_is_public_and_does_not_touch_storage(self):
        for path in ("/api/readings", "/api/exhibits", "/api/readings/example"):
            result, body = self.request("OPTIONS", path, auth=False, headers={
                "origin": "https://frontend.example", "access-control-request-method": "POST",
                "access-control-request-headers": "authorization,content-type"})
            self.assertEqual(result["statusCode"], 204)
            self.assertEqual(body, "")
            self.assertEqual(result["headers"]["access-control-allow-origin"], "*")
            self.assertEqual(result["headers"]["access-control-allow-methods"], "GET, POST, PATCH, PUT, OPTIONS")
            self.assertEqual(result["headers"]["access-control-allow-headers"], "Authorization, Content-Type, If-Match")
            self.assertNotIn("access-control-allow-credentials", result["headers"])
        self.assertEqual(self.table.calls, [])

    def test_success_and_error_responses_have_cors_without_cookie_credentials(self):
        for auth in (True, False):
            result, _ = self.request("GET", "/api/readings/missing", auth=auth, headers={"origin": "https://frontend.example"})
            self.assertEqual(result["statusCode"], 404 if auth else 401)
            self.assertEqual(result["headers"]["access-control-allow-origin"], "*")
            self.assertNotIn("access-control-allow-credentials", result["headers"])
        result, _ = self.request("GET", "/api/health", auth=False)
        self.assertEqual(result["headers"]["access-control-allow-origin"], "*")

    def test_openapi_examples_match_actual_post_get_and_conflict_contract(self):
        _, spec = self.request("GET", "/openapi.json", auth=False)
        exhibit_operation = spec["paths"]["/api/exhibits"]["post"]
        reading_operation = spec["paths"]["/api/readings"]["post"]
        exhibit = exhibit_operation["requestBody"]["content"]["application/json"]["example"]
        reading = reading_operation["requestBody"]["content"]["application/json"]["example"]
        for path, payload in (("/api/exhibits", exhibit), ("/api/readings", reading)):
            created, created_body = self.request("POST", path, payload)
            self.assertEqual(created["statusCode"], 201)
            self.assertEqual(created_body["status"], "created")
            duplicate, duplicate_body = self.request("POST", path, payload)
            self.assertEqual(duplicate["statusCode"], 200)
            self.assertEqual(duplicate_body["status"], "duplicate")
            self.assertEqual(duplicate_body["item"], created_body["item"])
            exact, exact_body = self.request("GET", created["headers"]["location"])
            self.assertEqual(exact["statusCode"], 200)
            self.assertEqual(exact_body["consistency"], "strong")
            self.assertEqual(exact_body["item"], created_body["item"])
        conflict, _ = self.request("POST", "/api/readings", dict(reading, temperature_c=30))
        self.assertEqual(conflict["statusCode"], 409)
        self.assertIn("409", reading_operation["responses"])
        self.assertIn("404", reading_operation["responses"])
        self.assertNotIn("/api/controls", spec["paths"])

    def test_openapi_schemas_describe_every_required_input_field_and_resolve_refs(self):
        _, spec = self.request("GET", "/openapi.json", auth=False)
        for name, example in (("ReadingInput", self.reading), ("ExhibitInput", self.exhibit)):
            schema = spec["components"]["schemas"][name]
            self.assertEqual(set(schema["properties"]), set(example))
            self.assertEqual(set(schema["required"]), set(example))
            self.assertFalse(schema["additionalProperties"])
        def inspect(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    target = spec
                    self.assertTrue(value["$ref"].startswith("#/"))
                    for part in value["$ref"][2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        inspect(spec)
        for path in ("/", "/docs", "/openapi.json", "/api/health"):
            self.assertEqual(spec["paths"][path]["get"]["security"], [])

    def test_all_data_routes_require_authentication(self):
        for method, path, data in (("GET", "/api/schema", None), ("POST", "/api/readings", self.reading),
                                   ("GET", "/api/readings/unknown", None), ("POST", "/api/exhibits", self.exhibit),
                                   ("GET", "/api/exhibits/unknown", None), ("GET", "/api/readings", None)):
            with self.subTest(path=path):
                result, _ = self.request(method, path, data, auth=False)
                self.assertEqual(result["statusCode"], 401)
                self.assertEqual(result["headers"]["www-authenticate"], "Bearer")
        self.assertEqual(self.table.calls, [])

    def test_invalid_and_unicode_tokens_fail_closed(self):
        for value in ("Bearer wrong", "Bearer 秘密", "Basic " + TOKEN, "Bearer " + TOKEN + " extra"):
            result, _ = self.request("GET", "/api/schema", headers={"authorization": value})
            self.assertEqual(result["statusCode"], 401)
        with patch.dict(os.environ, {"API_TOKEN": "short"}):
            result, _ = self.request("GET", "/api/schema")
            self.assertEqual(result["statusCode"], 503)

    def test_reading_upload_uses_atomic_insert_and_exact_strong_readback(self):
        self.create_exhibit()
        result, created = self.request("POST", "/api/readings", self.reading)
        self.assertEqual(result["statusCode"], 201)
        self.assertEqual(created["status"], "created")
        record_id = self.reading["reading_id"]
        stored = self.table.items[("READING#" + record_id, "META")]
        self.assertEqual(stored["GSI1PK"], "DEVICE#esp32-01")
        self.assertTrue(stored["GSI1SK"].endswith("#" + record_id))
        result, body = self.request("GET", "/api/readings/" + record_id)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(body["consistency"], "strong")
        self.assertEqual(body["item"], created["item"])
        self.assertNotIn("PK", body["item"])
        self.assertEqual(self.table.calls[-1], ("get_item", {"Key": {"PK": "READING#" + record_id, "SK": "META"}, "ConsistentRead": True}))
        self.assertEqual(result["headers"]["cache-control"], "no-store")

    def test_duplicate_payload_is_idempotent_and_preserves_receive_time(self):
        self.create_exhibit()
        _, first = self.request("POST", "/api/readings", self.reading)
        alternate = dict(self.reading, recorded_at=self.reading["recorded_at"].replace(".000Z", "Z"))
        result, second = self.request("POST", "/api/readings", alternate)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["item"], second["item"])
        self.assertEqual(len(self.table.items), 2)

    def test_changed_duplicate_returns_conflict_without_overwrite(self):
        self.create_exhibit()
        self.request("POST", "/api/readings", self.reading)
        before = copy.deepcopy(self.table.items)
        result, body = self.request("POST", "/api/readings", dict(self.reading, temperature_c=30))
        self.assertEqual(result["statusCode"], 409)
        self.assertEqual(body["error"]["code"], "id_conflict")
        self.assertEqual(before, self.table.items)

    def test_exhibits_are_immutable_and_duplicate_safe(self):
        self.create_exhibit()
        result, _ = self.request("POST", "/api/exhibits", self.exhibit)
        self.assertEqual(result["statusCode"], 200)
        result, _ = self.request("POST", "/api/exhibits", dict(self.exhibit, name="Changed name"))
        self.assertEqual(result["statusCode"], 409)
        _, body = self.request("GET", "/api/exhibits/" + self.exhibit["exhibit_id"])
        self.assertEqual(body["item"]["name"], self.exhibit["name"])

    def test_unknown_exhibit_prevents_orphan_reading(self):
        result, _ = self.request("POST", "/api/readings", self.reading)
        self.assertEqual(result["statusCode"], 404)
        self.assertFalse(any(call[0] == "put_item" for call in self.table.calls))

    def test_schema_rejects_unknown_missing_private_and_batch_fields_before_storage(self):
        invalids = [dict(self.reading, visitor_id="visitor-1"), dict(self.reading, brightness_lux=20),
                    {key: value for key, value in self.reading.items() if key != "device_id"},
                    [self.reading], None]
        for data in invalids:
            with self.subTest(data=data):
                result, _ = self.request("POST", "/api/readings", data)
                self.assertEqual(result["statusCode"], 400)
        self.assertEqual(self.table.calls, [])

    def test_numeric_types_bounds_and_precision(self):
        for key, value in (("temperature_c", True), ("temperature_c", "23"), ("temperature_c", -41),
                           ("temperature_c", 126), ("temperature_c", 1.0000001), ("brightness_raw", 4096),
                           ("brightness_raw", 1.5), ("brightness_raw", False), ("distance_cm", -1),
                           ("distance_cm", 1001), ("occupied", 1), ("source", "hardware-test")):
            with self.subTest(key=key, value=value):
                result, _ = self.request("POST", "/api/readings", dict(self.reading, **{key: value}))
                self.assertEqual(result["statusCode"], 400)
        self.assertEqual(self.table.calls, [])

    def test_numeric_boundaries_and_null_distance_are_accepted(self):
        self.create_exhibit()
        for index, value in enumerate((-40, 125)):
            reading = dict(self.reading, reading_id="boundary-" + str(index), temperature_c=value,
                           brightness_raw=0 if index == 0 else 4095, distance_cm=None)
            result, body = self.request("POST", "/api/readings", reading)
            self.assertEqual(result["statusCode"], 201)
            self.assertIsNone(body["item"]["distance_cm"])

    def test_timestamp_validity_and_future_bound(self):
        for value in ("2026-10-26 12:00:00", "2026-10-26T12:00:00+08:00", "2026-02-30T12:00:00Z", "2026-10-26T12:00:60Z",
                      (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")):
            with self.subTest(value=value):
                result, _ = self.request("POST", "/api/readings", dict(self.reading, recorded_at=value))
                self.assertEqual(result["statusCode"], 400)
        self.assertEqual(self.table.calls, [])

    def test_invalid_identifier_prevents_storage_and_path_injection(self):
        for value in ("../bad", "bad/id", "", "a" * 65, "white space", "#partition", "é"):
            result, _ = self.request("POST", "/api/readings", dict(self.reading, device_id=value))
            self.assertEqual(result["statusCode"], 400)
        result, _ = self.request("GET", "/api/readings/a/b")
        self.assertEqual(result["statusCode"], 400)
        self.assertEqual(self.table.calls, [])

    def test_exhibit_limits_and_field_lengths(self):
        for data in (dict(self.exhibit, temperature_min_c=30, temperature_max_c=20),
                     dict(self.exhibit, brightness_min_raw=3000, brightness_max_raw=2000),
                     dict(self.exhibit, name=" "), dict(self.exhibit, name="x" * 121),
                     dict(self.exhibit, description="x" * 2001), dict(self.exhibit, tag_id="invalid/tag")):
            result, _ = self.request("POST", "/api/exhibits", data)
            self.assertEqual(result["statusCode"], 400)
        self.assertEqual(self.table.calls, [])

    def test_invalid_json_duplicate_keys_nonfinite_and_size(self):
        for raw in ('{"a":1,"a":2}', '{"temperature_c":NaN}', '{"temperature_c":Infinity}', '{', 'null'):
            result, _ = self.request("POST", "/api/readings", raw_body=raw)
            self.assertEqual(result["statusCode"], 400)
        result, _ = self.request("POST", "/api/readings", raw_body='"' + "x" * app.MAX_BODY_BYTES + '"')
        self.assertEqual(result["statusCode"], 413)
        result, _ = self.request("POST", "/api/readings", self.reading, headers={"content-type": "text/plain"})
        self.assertEqual(result["statusCode"], 415)
        self.assertEqual(self.table.calls, [])

    def test_base64_body_is_validated_and_supported(self):
        self.create_exhibit()
        encoded = base64.b64encode(json.dumps(self.reading).encode()).decode()
        result, _ = self.request("POST", "/api/readings", raw_body=encoded, encoded=True)
        self.assertEqual(result["statusCode"], 201)
        result, _ = self.request("POST", "/api/readings", raw_body="@@@", encoded=True)
        self.assertEqual(result["statusCode"], 400)

    def test_query_is_indexed_scoped_ordered_and_bounded(self):
        self.create_exhibit()
        for reading in simulator.make_readings(3, "esp32-01", self.exhibit["exhibit_id"], "ordered"):
            self.request("POST", "/api/readings", reading)
        self.request("POST", "/api/readings", dict(self.reading, device_id="other-device"))
        result, body = self.request("GET", "/api/readings", query={"device_id": "esp32-01", "limit": "2"})
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual([item["reading_id"] for item in body["items"]], ["ordered-003", "ordered-002"])
        self.assertEqual(body["consistency"], "eventual")
        self.assertTrue(body["truncated"])
        self.assertEqual(self.table.calls[-1][0], "query")
        self.assertFalse(self.table.calls[-1][1]["ScanIndexForward"])
        self.assertNotIn("ConsistentRead", self.table.calls[-1][1])

    def test_query_rejects_unbounded_or_unknown_parameters(self):
        for query in ({}, {"device_id": "esp32-01", "limit": "0"}, {"device_id": "esp32-01", "limit": "101"},
                      {"device_id": "esp32-01", "limit": "1.5"}, {"device_id": "esp32-01", "limit": "all"},
                      {"device_id": "esp32-01", "visitor": "name"}):
            result, _ = self.request("GET", "/api/readings", query=query)
            self.assertEqual(result["statusCode"], 400)
        self.assertEqual(self.table.calls, [])

    def test_missing_record_unknown_route_and_unsupported_mutation(self):
        result, _ = self.request("GET", "/api/readings/missing")
        self.assertEqual(result["statusCode"], 404)
        result, _ = self.request("GET", "/api/unknown")
        self.assertEqual(result["statusCode"], 404)
        result, _ = self.request("DELETE", "/api/readings/missing")
        self.assertEqual(result["statusCode"], 405)
        result, _ = self.request("POST", "/api/readings", self.reading, query={"other": "1"})
        self.assertEqual(result["statusCode"], 400)

    def test_storage_failure_is_sanitized_and_not_reported_as_success(self):
        secret = "sensitive-internal-host-and-key"
        with patch.object(self.table, "get_item", side_effect=RuntimeError(secret)):
            result, body = self.request("GET", "/api/readings/missing")
        self.assertEqual(result["statusCode"], 503)
        self.assertNotIn(secret, json.dumps(body))

    def test_simulator_integration_uploads_and_verifies_12_records_in_memory(self):
        test = self
        class LocalClient:
            def request(self, path, method="GET", payload=None):
                result, body = test.request(method, path, payload)
                if result["statusCode"] >= 400:
                    raise AssertionError(body)
                return body
        readings = list(simulator.make_readings(12, "ec2-demo-01", self.exhibit["exhibit_id"], "simulated"))
        result = simulator.run_demo(LocalClient(), readings, self.exhibit)
        self.assertEqual(result["verified_count"], 12)
        self.assertEqual(result["data_kind"], "synthetic_demo")
        self.assertFalse(result["hardware_connected"])
        self.assertEqual(len(self.table.items), 13)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertTrue(all(item["consistency"] == "strong" for item in result["readings"]))
        repeated = simulator.run_demo(LocalClient(), readings, self.exhibit)
        self.assertTrue(all(item["upload_status"] == "duplicate" for item in repeated["readings"]))
        self.assertEqual(len(self.table.items), 13)


class SimulatorTests(unittest.TestCase):
    def test_client_requires_https_valid_token_and_safe_rate(self):
        for url, token, interval in (("http://example.test", TOKEN, 1), ("https://user:pass@example.test", TOKEN, 1),
                                     ("https://example.test?secret=x", TOKEN, 1), ("https://example.test", "short", 1),
                                     ("https://example.test", TOKEN, 0.1)):
            with self.assertRaises(ValueError):
                simulator.DemoClient(url, token, interval)
        simulator.DemoClient("https://example.test", TOKEN, 1)

    def test_simulator_never_follows_redirects_with_credentials(self):
        self.assertIsNone(simulator.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.test"))

    def test_ssm_configuration_uses_decryption_region_and_never_outputs_secret(self):
        outputs = [unittest.mock.Mock(returncode=0, stdout="https://example.test\n"),
                   unittest.mock.Mock(returncode=0, stdout=TOKEN + "\n")]
        with patch.object(simulator.subprocess, "run", side_effect=outputs) as run:
            url, token = simulator.ssm_config("us-east-1")
        self.assertEqual(url, "https://example.test")
        self.assertEqual(token, TOKEN)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all(call[call.index("--region") + 1] == "us-east-1" for call in calls))
        self.assertNotIn("--with-decryption", calls[0])
        self.assertIn("--with-decryption", calls[1])
        self.assertNotIn(TOKEN, json.dumps(calls))

    def test_simulator_detects_mismatched_or_weak_readback(self):
        for result in ({"consistency": "eventual", "item": {"reading_id": "same"}},
                       {"consistency": "strong", "item": {"reading_id": "different", "received_at": "now"}}):
            with self.assertRaises(RuntimeError):
                simulator.verify_readback({"reading_id": "same"}, result)


if __name__ == "__main__":
    unittest.main()
