"""Exercise the public API adapter with real multipart bytes and mocked Cloudflare."""
import base64
from contextlib import redirect_stdout
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import queue
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import lambda_handler
import server

ORIGIN = "https://dkbznu9f5nh9n.cloudfront.net"
ACCOUNT = "a" * 32
TOKEN = "test-token-that-is-not-a-real-secret"
IMAGE = b"\xff\xd8\xfftest-image"
CONTENT_TYPE = "multipart/form-data; boundary=reshow-test"


def multipart(account=ACCOUNT, token=TOKEN, image=IMAGE, image_type="image/jpeg", task=None, stage=None, previous_parts=None, extra=b""):
    body = b""
    fields = [("account_id", account), ("api_token", token)]
    if task is not None:
        fields.append(("task", task))
    if stage is not None:
        fields.append(("stage", stage))
    if previous_parts is not None:
        fields.append(("previous_parts", json.dumps(previous_parts)))
    for name, value in fields:
        body += b'--reshow-test\r\nContent-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n' + value.encode() + b"\r\n"
    body += b'--reshow-test\r\nContent-Disposition: form-data; name="image"; filename="image.jpg"\r\nContent-Type: ' + image_type.encode() + b"\r\n\r\n" + image + b"\r\n"
    return body + extra + b"--reshow-test--\r\n"


def event(body=None, path=lambda_handler.GENERATE_PATH, content_type=CONTENT_TYPE, **headers):
    body = multipart() if body is None else body
    return {"rawPath": path,
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"origin": ORIGIN, "content-type": content_type,
                        "x-amz-content-sha256": hashlib.sha256(body).hexdigest(), **headers},
            "isBase64Encoded": True, "body": base64.b64encode(body).decode()}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"RESHOW_ALLOWED_ORIGIN": ORIGIN})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_tripo_retexture_uses_source_task_and_text(self):
        body = json.dumps({
            "action": "retexture", "api_key": TOKEN,
            "source_task_id": "image-task-123", "prompt": "  worn leather  ",
        }).encode()
        with patch.object(server, "tripo_request", return_value={"task_id": "task-123"}) as outbound:
            result = lambda_handler.handler(event(body, content_type="application/json"), None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"])["data"], {"task_id": "task-123"})
        path, token, payload = outbound.call_args.args
        self.assertEqual(path, "/models/texture")
        self.assertEqual(token, TOKEN)
        self.assertEqual(json.loads(payload), {
            "input": "image-task-123", "texture_prompt": {"text": "worn leather"},
            "texture_quality": "standard", "pbr": True,
        })

    def test_tripo_image_upload_and_status(self):
        body = json.dumps({
            "action": "image", "api_key": TOKEN,
            "image": base64.b64encode(IMAGE).decode(),
        }).encode()
        with patch.object(server, "tripo_request", side_effect=[
            {"file_token": "file-123"}, {"task_id": "task-123"},
        ]) as outbound:
            result = lambda_handler.handler(event(body, content_type="application/json"), None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(outbound.call_args_list[0].args[0], "/files")
        self.assertIn(IMAGE, outbound.call_args_list[0].args[2])
        self.assertEqual(outbound.call_args_list[1].args[0], "/generation/image-to-model")
        self.assertEqual(json.loads(outbound.call_args_list[1].args[2]), {
            "model": server.TRIPO_MODEL, "input": "file-123", "texture": True,
            "pbr": True, "texture_quality": "standard",
        })
        body = json.dumps({"action": "status", "api_key": TOKEN, "task_id": "task-123"}).encode()
        reply = {"status": "success", "type": "image_to_model", "progress": 100, "credits_consumed": 30,
                 "output": {"model_url": "https://cdn.tripo3d.ai/model.glb"}}
        with patch.object(server, "tripo_request", return_value=reply):
            result = lambda_handler.handler(event(body, content_type="application/json"), None)
        self.assertEqual(json.loads(result["body"])["data"]["model_url"], reply["output"]["model_url"])
        self.assertEqual(json.loads(result["body"])["data"]["credits_consumed"], 30)
        self.assertEqual(json.loads(result["body"])["data"]["type"], "image_to_model")

    def test_tripo_rejects_bad_inputs_and_untrusted_result_urls(self):
        for request in [
            {"action": "text", "api_key": TOKEN, "prompt": "A rabbit"},
            {"action": "image", "api_key": TOKEN, "image": "not-base64"},
            {"action": "status", "api_key": TOKEN, "task_id": "../account"},
            {"action": "retexture", "api_key": TOKEN, "source_task_id": "../account", "prompt": "gold"},
            {"action": "retexture", "api_key": TOKEN, "source_task_id": "task-123", "prompt": " "},
            {"action": "retexture", "api_key": TOKEN, "source_task_id": "task-123", "prompt": "x" * 1025},
        ]:
            with self.subTest(request=request), patch.object(server, "tripo_request") as outbound:
                body = json.dumps(request).encode()
                self.assertEqual(lambda_handler.handler(event(body, content_type="application/json"), None)["statusCode"], 400)
                outbound.assert_not_called()
        with patch.object(server, "tripo_request", return_value={
            "status": "success", "output": {"model_url": "http://example.com/model.glb"},
        }):
            body = json.dumps({"action": "status", "api_key": TOKEN, "task_id": "task-123"}).encode()
            self.assertEqual(lambda_handler.handler(event(body, content_type="application/json"), None)["statusCode"], 502)

    def test_tripo_transport_never_echoes_upstream_secrets_or_follows_redirects(self):
        opener = unittest.mock.MagicMock()
        opener.open.side_effect = server.HTTPError(
            "https://openapi.tripo3d.ai/v3/generation/text-to-model",
            401, "Bearer " + TOKEN, {}, None,
        )
        with patch.object(server, "build_opener", return_value=opener) as build:
            with self.assertRaises(RuntimeError) as caught:
                server.tripo_request("/generation/text-to-model", TOKEN, b"{}")
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIsInstance(build.call_args.args[0], server.NoRedirects)

    def test_real_post_parses_binary_image_and_generates_without_accepting_terms(self):
        with patch.object(server, "cloudflare_request", return_value=(200, {
            "success": True, "result": {"response": '{"name":"Helmet","description":"A metal helmet."}'},
        })) as outbound:
            result = lambda_handler.handler(event(), None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"])["data"]["name"], "Helmet")
        self.assertEqual(result["headers"]["cache-control"], "no-store, private")
        outbound.assert_called_once()
        endpoint, token, payload = outbound.call_args.args
        self.assertEqual(endpoint, "https://api.cloudflare.com/client/v4/accounts/" + ACCOUNT + "/ai/run/@cf/meta/llama-3.2-11b-vision-instruct")
        self.assertEqual(token, TOKEN)
        self.assertEqual(bytes(payload["image"]), IMAGE)
        self.assertNotEqual(payload["prompt"], "agree")

    def test_workers_ai_generates_validated_threejs_group_parts(self):
        parts = [
            {"shape": "box" if index % 2 else "cylinder",
             "position": [0, index / 10, 0],
             "scale": [0.5, 0.5, 0.5], "color": "#aabbcc"}
            for index in range(8)
        ]
        with patch.object(server, "cloudflare_request", return_value=(200, {
            "success": True, "result": {"response": json.dumps({"parts": parts})},
        })) as outbound:
            result = lambda_handler.handler(
                event(multipart(task="threejs_group")), None
            )
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"])["data"]["parts"], parts)
        payload = outbound.call_args.args[2]
        self.assertIn("Three.js model", payload["prompt"])
        self.assertEqual(payload["max_tokens"], 900)
        self.assertEqual(outbound.call_args.kwargs["timeout"], 55)

    def test_workers_ai_builds_threejs_group_in_incremental_steps(self):
        def part(index):
            return {
                "shape": "box",
                "position": [index / 10, index / 8, 0],
                "scale": [0.5, 0.6, 0.4],
                "color": "#aabbcc",
            }

        generated_steps = [
            [part(0), part(1), part(2)],
            [part(3), part(4), part(5)],
            [part(6), part(7)],
        ]
        replies = [
            (200, {
                "success": True,
                "result": {"response": json.dumps({"parts": parts})},
            })
            for parts in generated_steps
        ]
        previous_parts = []
        with patch.object(server, "cloudflare_request", side_effect=replies) as outbound:
            for stage, new_parts, total in (
                ("silhouette", generated_steps[0], 3),
                ("structure", generated_steps[1], 6),
                ("details", generated_steps[2], 8),
            ):
                result = lambda_handler.handler(
                    event(multipart(
                        task="threejs_group",
                        stage=stage,
                        previous_parts=previous_parts,
                    )),
                    None,
                )
                self.assertEqual(result["statusCode"], 200)
                data = json.loads(result["body"])["data"]
                self.assertEqual(data["stage"], stage)
                self.assertEqual(len(data["parts"]), total)
                self.assertEqual(data["parts"], previous_parts + new_parts)
                previous_parts = data["parts"]

        self.assertEqual(outbound.call_count, 3)
        prompts = [call.args[2]["prompt"] for call in outbound.call_args_list]
        self.assertIn("silhouette step", prompts[0])
        self.assertIn("structure step", prompts[1])
        self.assertIn(json.dumps(generated_steps[0], separators=(",", ":")), prompts[1])
        self.assertIn("details step", prompts[2])
        self.assertIn(json.dumps(generated_steps[0] + generated_steps[1], separators=(",", ":")), prompts[2])
        self.assertTrue(all(call.kwargs["timeout"] == 55 for call in outbound.call_args_list))

    def test_workers_ai_rejects_out_of_order_threejs_steps_before_calling_model(self):
        with patch.object(server, "cloudflare_request") as outbound:
            result = lambda_handler.handler(
                event(multipart(
                    task="threejs_group",
                    stage="structure",
                    previous_parts=[],
                )),
                None,
            )
        self.assertEqual(result["statusCode"], 400)
        self.assertIn("3D model step is invalid", result["body"])
        outbound.assert_not_called()

    def test_workers_ai_rejects_invalid_threejs_group_parts(self):
        valid_part = {
            "shape": "box",
            "position": [0, 0, 0],
            "scale": [0.5, 0.5, 0.5],
            "color": "#aabbcc",
        }
        cases = [
            [dict(valid_part) for _ in range(7)],
            [dict(valid_part) for _ in range(8)],
            [dict(valid_part) for _ in range(8)],
            [{"shape": "sphere", "position": [0, 0, 0],
              "scale": [0.5, 0.5, 0.5], "color": "#aabbcc"}
             for _ in range(8)],
        ]
        cases[1][0]["scale"] = [4, 0.5, 0.5]
        cases[2][0]["shape"] = "run_javascript"
        for parts in cases:
            with self.subTest(parts=parts):
                with patch.object(server, "cloudflare_request", return_value=(200, {
                    "success": True,
                    "result": {"response": json.dumps({"parts": parts})},
                })) as outbound:
                    result = lambda_handler.handler(
                        event(multipart(task="threejs_group")), None
                    )
                self.assertEqual(result["statusCode"], 502)
                self.assertIn("3D model", result["body"])
                if parts is cases[-1]:
                    self.assertIn("sphere-heavy", result["body"])
                outbound.assert_called_once()

    def test_parser_rejects_unknown_workers_ai_task(self):
        with patch.object(server, "cloudflare_request") as outbound:
            result = lambda_handler.handler(
                event(multipart(task="execute_code")), None
            )
        self.assertEqual(result["statusCode"], 400)
        outbound.assert_not_called()

    def test_exhibit_names_preserve_sentence_prefix_and_clause_boundaries(self):
        cases = {
            "The image shows a bronze helmet with a crest.": "Bronze helmet",
            "An ornate vase THAT has a lid. Another sentence.": "Ornate vase",
            "The image features an ancient urn characterized by blue glazing.": "Ancient urn",
            "A model characterized  by blue glazing.": "Model characterized  by blue glazing",
            "A metal artifact, which has scratches.": "Metal artifact",
            "The image depicts a wooden box. It is old.": "Wooden box",
            "An artifact with": "Artifact with",
            "A helmet characterized by": "Helmet characterized by",
            "Artifact\u2003with details": "Artifact",
            "with a rim": "With a rim",
            " with a rim": "Image-based exhibit",
            "": "Image-based exhibit",
        }
        for description, expected in cases.items():
            with self.subTest(description=description):
                self.assertEqual(server.create_exhibit_name(description), expected)

    def test_exhibit_name_parsing_finishes_on_long_whitespace_without_a_clause(self):
        # The former unanchored whitespace regex retried the entire whitespace
        # suffix at each position when no clause followed it (quadratic work).
        subprocess.run([sys.executable, "-c", (
            'import server; description="Artifact" + " " * 200000 + "remainder"; '
            'assert server.create_exhibit_name(description) == description'
        )], check=True, timeout=5)


    def test_invalid_uploads_fail_before_any_outbound_request(self):
        cases = [(multipart(account="../outside"), 400),
                 (multipart(token="x\r\nAuthorization: attacker"), 400),
                 (multipart(image=b"not-an-image"), 415),
                 (multipart(image=IMAGE + b"x" * server.MAX_IMAGE_SIZE), 413),
                 (multipart(extra=b'--reshow-test\r\nContent-Disposition: form-data; name="api_token"\r\n\r\nextra\r\n'), 400)]
        with patch.object(server, "cloudflare_request") as outbound:
            for body, expected in cases:
                with self.subTest(expected=expected, size=len(body)):
                    self.assertEqual(lambda_handler.handler(event(body), None)["statusCode"], expected)
            outbound.assert_not_called()

    def test_cross_origin_checksum_and_binary_encoding_are_validated(self):
        cases = [event(origin="https://attacker.example"), event(**{"x-amz-content-sha256": "0" * 64}), event()]
        cases[-1]["body"] = "not base64"
        with patch.object(server, "cloudflare_request") as outbound:
            self.assertEqual([lambda_handler.handler(value, None)["statusCode"] for value in cases], [403, 400, 400])
            outbound.assert_not_called()

    def test_upstream_failure_never_echoes_or_logs_credentials(self):
        capture = io.StringIO()
        with redirect_stdout(capture), patch.object(server, "cloudflare_request", return_value=(403, {
            "errors": [{"message": "Bearer " + TOKEN + " " + ACCOUNT}], "success": False,
        })):
            result = lambda_handler.handler(event(), None)
        self.assertEqual(result["statusCode"], 502)
        self.assertIn("HTTP 403", result["body"])
        for secret in (TOKEN, ACCOUNT):
            self.assertNotIn(secret, result["body"] + capture.getvalue())

    def test_routes_and_health_do_not_contact_cloudflare(self):
        health = {"rawPath": "/api/health", "requestContext": {"http": {"method": "GET"}}}
        with patch.object(server, "cloudflare_request") as outbound, patch.object(Path, "read_text", return_value=json.dumps({"commit": "b" * 40})):
            result = lambda_handler.handler(health, None)
            self.assertEqual(json.loads(result["body"])["commit"], "b" * 40)
            unknown = event()
            unknown["rawPath"] = "/api/not-a-route"
            self.assertEqual(lambda_handler.handler(unknown, None)["statusCode"], 404)
            unknown["rawPath"] = lambda_handler.GENERATE_PATH
            unknown["requestContext"]["http"]["method"] = "GET"
            self.assertEqual(lambda_handler.handler(unknown, None)["statusCode"], 405)
            outbound.assert_not_called()

    def test_cloudflare_transport_does_not_follow_redirects_and_bounds_responses(self):
        opener = unittest.mock.MagicMock()
        reply = opener.open.return_value.__enter__.return_value
        reply.status = 200
        reply.read.return_value = b'{"success": true}'
        with patch.object(server, "build_opener", return_value=opener) as build:
            server.cloudflare_request("https://api.cloudflare.com/endpoint", TOKEN, {"image": []})
            self.assertIsInstance(build.call_args.args[0], server.NoRedirects)
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 40)
            self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer " + TOKEN)
            reply.read.assert_called_once_with(server.MAX_RESPONSE_SIZE + 1)
            reply.read.return_value = b"x" * (server.MAX_RESPONSE_SIZE + 1)
            with self.assertRaisesRegex(RuntimeError, "oversized"):
                server.cloudflare_request("https://api.cloudflare.com/endpoint", TOKEN, {})
        self.assertIsNone(server.NoRedirects().redirect_request(None, None, 302, "", {}, "https://attacker.example"))


class LocalHttpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.certificates = tempfile.TemporaryDirectory()
        cls.certificate_directory = Path(cls.certificates.name)
        cls.certificate = cls.certificate_directory / "localhost.pem"
        cls.private_key = cls.certificate_directory / "localhost.key"
        configuration = cls.certificate_directory / "openssl.cnf"
        configuration.write_text(
            "[req]\ndistinguished_name=subject\nx509_extensions=localhost\nprompt=no\n"
            "[subject]\nCN=localhost\n[localhost]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
        )
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(cls.private_key), "-out", str(cls.certificate), "-config", str(configuration),
        ], check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.certificates.cleanup()

    def test_local_startup_configures_tls_before_serving_and_reports_https(self):
        output = io.StringIO()
        arguments = ["server.py", "--cert-file", str(self.certificate), "--key-file", str(self.private_key)]
        with patch.object(sys, "argv", arguments), patch("http.server.ThreadingHTTPSServer") as httpd, patch.object(server.ssl, "SSLContext") as context, redirect_stdout(output):
            httpd.return_value.server_port = 8000
            httpd.return_value.serve_forever.side_effect = KeyboardInterrupt
            server.main()
        context.assert_called_once_with(ssl.PROTOCOL_TLS_SERVER)
        self.assertEqual(context.return_value.minimum_version, ssl.TLSVersion.TLSv1_2)
        context.return_value.load_cert_chain.assert_called_once_with(certfile=str(self.certificate), keyfile=str(self.private_key))
        httpd.assert_called_once()
        self.assertEqual(httpd.call_args.kwargs["certfile"], str(self.certificate))
        self.assertEqual(httpd.call_args.kwargs["keyfile"], str(self.private_key))
        context.return_value.wrap_socket.assert_not_called()
        self.assertIn("ReShow HTTPS is listening on 127.0.0.1:8000", output.getvalue())
        httpd.return_value.server_close.assert_called_once()

    def test_local_startup_without_certificate_arguments_fails_before_binding(self):
        with patch.object(sys, "argv", ["server.py"]), patch("http.server.ThreadingHTTPSServer") as httpd, patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                server.main()
        self.assertEqual(error.exception.code, 2)
        httpd.assert_not_called()

    def test_local_startup_with_missing_or_invalid_certificates_fails_before_binding(self):
        missing = self.certificate_directory / "missing.pem"
        invalid = self.certificate_directory / "invalid.pem"
        invalid.write_text("not a TLS certificate or key")
        cases = [(missing, self.private_key), (invalid, self.private_key),
                 (self.certificate, missing), (self.certificate, invalid)]
        for certificate, key in cases:
            arguments = ["server.py", "--cert-file", str(certificate), "--key-file", str(key)]
            with self.subTest(certificate=certificate.name, key=key.name), patch.object(sys, "argv", arguments), patch("http.server.ThreadingHTTPSServer") as httpd:
                with self.assertRaises(OSError):
                    server.main()
                httpd.assert_not_called()

    def test_local_tls_server_accepts_verified_https_rejects_http_and_keeps_backend_private(self):
        created = queue.Queue()
        from http.server import ThreadingHTTPSServer
        http_server = ThreadingHTTPSServer

        def capture_server(*args, **kwargs):
            instance = http_server(*args, **kwargs)
            created.put(instance)
            return instance

        arguments = ["server.py", "--port", "0", "--cert-file", str(self.certificate), "--key-file", str(self.private_key)]
        with patch.object(sys, "argv", arguments), patch("http.server.ThreadingHTTPSServer", side_effect=capture_server):
            thread = threading.Thread(target=server.main, daemon=True)
            thread.start()
            httpd = created.get(timeout=5)
            try:
                context = ssl.create_default_context(cafile=str(self.certificate))
                connection = http.client.HTTPSConnection("127.0.0.1", httpd.server_port, context=context, timeout=5)
                connection.connect()
                self.assertIn(connection.sock.version(), ("TLSv1.2", "TLSv1.3"))
                self.assertEqual(httpd.socket.context.minimum_version, ssl.TLSVersion.TLSv1_2)
                for path, expected in (("/", 200), ("/js/app.js", 200), ("/server.py", 404), ("/lambda_handler.py", 404), ("/.git/config", 404), ("/js/", 404)):
                    connection.request("GET", path)
                    reply = connection.getresponse()
                    self.assertEqual(reply.status, expected, path)
                    reply.read()
                connection.close()
                plaintext = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
                try:
                    with self.assertRaises((OSError, http.client.HTTPException)):
                        plaintext.request("GET", "/")
                        plaintext.getresponse()
                finally:
                    plaintext.close()
            finally:
                httpd.shutdown()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())


class LocalRfidDeviceEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.StudioHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=5)
        cls.httpd.server_close()

    def setUp(self):
        self.epaper_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.epaper_dir.cleanup)
        patcher = patch.object(server, "EPAPER_DESCRIPTION_FILE", Path(self.epaper_dir.name) / "description.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        with server.RFID_EVENT_LOCK:
            server.RFID_LATEST_DEVICE_EVENT = None
            server.EPAPER_DELIVERED_DESCRIPTION = None
            server.RFID_WORKFLOW.update({
                "phase": "idle", "uid": None, "exhibit_id": None, "edit_id": None,
            })

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_local_server_generates_and_routes_threejs_group_task(self):
        parts = [
            {"shape": "box", "position": [0, index / 10, 0],
             "scale": [0.5, 0.5, 0.5], "color": "#aabbcc"}
            for index in range(8)
        ]
        with patch.object(server, "cloudflare_request", return_value=(200, {
            "success": True, "result": {"response": json.dumps({"parts": parts})},
        })) as outbound:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.port, timeout=5
            )
            connection.request(
                "POST",
                "/api/technology/generate-exhibit",
                body=multipart(task="threejs_group"),
                headers={"Content-Type": CONTENT_TYPE},
            )
            response = connection.getresponse()
            status, result = response.status, json.loads(response.read())
            connection.close()
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["parts"], parts)
        self.assertEqual(outbound.call_count, 1)

    def test_device_event_get_returns_latest_post_in_the_same_shape(self):
        self.assertEqual(
            self.request("GET", "/api/rfid/device-event"),
            (404, {"error": "No RFID device event has been received yet."}),
        )
        posted_event = {
            "type": "rfid_event",
            "uid": "04833912EE1B90",
            "result": "accepted",
            "exhibit_id": "helmet-001",
        }
        status, response = self.request("POST", "/api/rfid/device-event", posted_event)
        self.assertEqual(status, 200)
        self.assertEqual(response, posted_event)
        status, response = self.request("GET", "/api/rfid/device-event")
        self.assertEqual(status, 200)
        self.assertEqual(response, posted_event)

    def test_device_event_rejects_bad_payload_without_replacing_latest(self):
        accepted = {
            "type": "rfid_event",
            "uid": "04833912EE1B90",
            "result": "accepted",
            "exhibit_id": "helmet-001",
        }
        self.request("POST", "/api/rfid/device-event", accepted)
        status, _ = self.request("POST", "/api/rfid/device-event", {
            "type": "rfid_event",
            "uid": "not-a-uid",
            "result": "accepted",
            "exhibit_id": "helmet-001",
        })
        self.assertEqual(status, 400)
        self.assertEqual(self.request("GET", "/api/rfid/device-event"), (200, accepted))

    def test_device_card_roles_drive_local_workflow(self):
        denied = {"type": "rfid_event", "uid": "DEADBEEF", "result": "denied"}
        self.assertEqual(self.request("POST", "/api/rfid/device-event", denied), (200, denied))
        self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["phase"], "idle")
        for role in ("door", "epaper"):
            card = {
                "type": "rfid_event", "uid": "A1B2C3D4", "result": "accepted",
                "exhibit_id": "helmet-001", "role": role,
            }
            self.assertEqual(self.request("POST", "/api/rfid/device-event", card), (200, card))
            workflow = self.request("GET", "/api/rfid/status")[1]["data"]
            self.assertEqual(workflow["phase"], "pending_edit")
            self.assertEqual(workflow["uid"], card["uid"])
            self.assertEqual(workflow["exhibit_id"], "helmet-001")
            self.assertTrue(workflow["edit_id"])
            other = {**card, "uid": "11223344"}
            self.assertEqual(self.request("POST", "/api/rfid/device-event", other), (200, other))
            self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["uid"], card["uid"])
            status, response = self.request("POST", "/api/rfid/staff-ready", {})
            self.assertEqual((status, response["data"]), (200, {
                "phase": "idle", "uid": None, "exhibit_id": None, "edit_id": None,
            }))
            self.assertEqual(self.request("POST", "/api/rfid/staff-ready", {})[0], 409)

    def test_edit_card_finishes_pending_workflow(self):
        door = {"type": "rfid_event", "uid": "A1B2C3D4", "result": "accepted",
                "exhibit_id": "helmet-001", "role": "door"}
        edit = {**door, "uid": "AABBCCDD", "role": "edit"}
        self.request("POST", "/api/rfid/device-event", door)
        self.assertEqual(self.request("POST", "/api/rfid/device-event", edit), (200, edit))
        self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["phase"], "idle")

    def test_legacy_second_scan_cannot_finish_update(self):
        door = {"type": "rfid_event", "uid": "A1B2C3D4", "result": "accepted",
                "exhibit_id": "helmet-001", "role": "door"}
        self.request("POST", "/api/rfid/device-event", door)
        legacy = {"uid": door["uid"], "result": "accepted", "door": "closed",
                  "phase": "second_scan_confirmed", "exhibit_id": "helmet-001"}
        self.assertEqual(self.request("POST", "/api/rfid/access-event", legacy)[0], 409)
        self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["phase"], "pending_edit")

    def test_epaper_status_returns_current_phase_as_plain_text(self):
        door = {
            "type": "rfid_event", "uid": "A1B2C3D4", "result": "accepted",
            "exhibit_id": "helmet-001", "role": "door",
        }
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/api/rfid/epaper-status")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"idle")
            self.assertEqual(self.request("POST", "/api/rfid/device-event", door), (200, door))
            connection.request("GET", "/api/rfid/epaper-status")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"pending_edit")
        finally:
            connection.close()

    def test_description_is_saved_for_epaper_only_for_current_session(self):
        card = {"type": "rfid_event", "uid": "A1B2C3D4", "result": "accepted",
                "exhibit_id": "helmet-001", "role": "door"}
        self.assertEqual(self.request("GET", "/api/rfid/epaper-description")[0], 404)
        self.request("POST", "/api/rfid/device-event", card)
        edit_id = self.request("GET", "/api/rfid/status")[1]["data"]["edit_id"]
        payload = {"edit_id": edit_id, "exhibit_id": "ex-014", "description": "A blue vase."}
        for invalid in ("", "Hello\nworld", "中文", "x" * 181):
            self.assertEqual(self.request("POST", "/api/rfid/publish-description", {**payload, "description": invalid})[0], 400)
        self.assertEqual(self.request("POST", "/api/rfid/publish-description", {**payload, "edit_id": "stale"})[0], 409)
        self.assertEqual(self.request("POST", "/api/rfid/publish-description", payload)[0], 200)
        self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["phase"], "idle")
        self.assertEqual(self.request("POST", "/api/rfid/publish-description", payload)[0], 409)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/api/rfid/epaper-description")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"A blue vase.")
        finally:
            connection.close()
        self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["phase"], "idle")
        self.request("POST", "/api/rfid/device-event", {**card, "role": "epaper"})
        self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["phase"], "pending_edit")
        self.assertEqual(json.loads(server.EPAPER_DESCRIPTION_FILE.read_text(encoding="utf-8"))["exhibit_id"], "ex-014")

    def test_description_write_failure_does_not_finish_edit(self):
        card = {"type": "rfid_event", "uid": "A1B2C3D4", "result": "accepted",
                "exhibit_id": "helmet-001", "role": "epaper"}
        self.request("POST", "/api/rfid/device-event", card)
        edit_id = self.request("GET", "/api/rfid/status")[1]["data"]["edit_id"]
        payload = {"edit_id": edit_id, "exhibit_id": "ex-014", "description": "A blue vase."}
        with patch.object(server.os, "replace", side_effect=OSError("test failure")):
            self.assertEqual(self.request("POST", "/api/rfid/publish-description", payload)[0], 500)
        self.assertEqual(self.request("GET", "/api/rfid/status")[1]["data"]["edit_id"], edit_id)
        self.assertEqual(self.request("GET", "/api/rfid/epaper-description")[0], 404)

    def test_delivery_confirmation_tracks_latest_saved_description(self):
        self.assertEqual(self.request("GET", "/api/rfid/epaper-delivery")[1]["data"]["state"], "not_saved")
        card = {"type": "rfid_event", "uid": "A1B2C3D4", "result": "accepted",
                "exhibit_id": "helmet-001", "role": "epaper"}

        def save(description):
            self.request("POST", "/api/rfid/device-event", card)
            edit_id = self.request("GET", "/api/rfid/status")[1]["data"]["edit_id"]
            return self.request("POST", "/api/rfid/publish-description", {
                "edit_id": edit_id, "exhibit_id": "ex-014", "description": description,
            })[0]

        def confirm(description):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                connection.request("POST", "/api/rfid/epaper-delivery", description.encode("ascii"),
                                   {"Content-Type": "text/plain"})
                response = connection.getresponse()
                response.read()
                return response.status
            finally:
                connection.close()

        self.assertEqual(save("Blue vase"), 200)
        self.assertEqual(self.request("GET", "/api/rfid/epaper-delivery")[1]["data"]["state"], "pending")
        self.assertEqual(confirm("Blue vase"), 200)
        self.assertEqual(self.request("GET", "/api/rfid/epaper-delivery")[1]["data"]["state"], "confirmed")
        self.assertEqual(save("Green vase"), 200)
        self.assertEqual(confirm("Blue vase"), 409)
        self.assertEqual(self.request("GET", "/api/rfid/epaper-delivery")[1]["data"]["state"], "pending")
        self.assertEqual(confirm("Green vase"), 200)
        self.assertEqual(self.request("GET", "/api/rfid/epaper-delivery")[1]["data"]["state"], "confirmed")


if __name__ == "__main__":
    unittest.main()
