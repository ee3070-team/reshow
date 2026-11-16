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


def hunyuan_multipart(secret_id="a" * 36, secret_key="test-tencent-key", region="ap-hongkong", image=IMAGE):
    body = b""
    fields = [
        ("secret_id", secret_id),
        ("secret_key", secret_key),
        ("region", region),
    ]
    for name, value in fields:
        body += b'--reshow-test\r\nContent-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n' + value.encode() + b"\r\n"
    body += b'--reshow-test\r\nContent-Disposition: form-data; name="image"; filename="image.jpg"\r\nContent-Type: image/jpeg\r\n\r\n' + image + b"\r\n"
    return body + b"--reshow-test--\r\n"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"RESHOW_ALLOWED_ORIGIN": ORIGIN})
        self.environment.start()
        self.addCleanup(self.environment.stop)

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

    def test_tencent_hunyuan_submit_sends_glb_image_without_exposing_credentials(self):
        body = hunyuan_multipart()
        with patch.object(server, "hunyuan_api_request", return_value={"JobId": "job-123"}) as outbound:
            result = lambda_handler.handler(
                event(body, path=server.HUNYUAN_SUBMIT_PATH), None
            )
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"]), {"data": {"job_id": "job-123"}})
        action, payload, secret_id, secret_key, region = outbound.call_args.args
        self.assertEqual(action, "SubmitHunyuanTo3DRapidJob")
        self.assertEqual(payload["ImageBase64"], base64.b64encode(IMAGE).decode("ascii"))
        self.assertEqual(payload["ResultFormat"], "GLB")
        self.assertEqual((secret_id, secret_key, region), ("a" * 36, "test-tencent-key", "ap-hongkong"))
        self.assertNotIn(secret_id, result["body"])
        self.assertNotIn(secret_key, result["body"])

    def test_tencent_hunyuan_query_returns_only_safe_result_links(self):
        body = json.dumps({
            "job_id": "job-123",
            "secret_id": "a" * 36,
            "secret_key": "test-tencent-key",
            "region": "ap-hongkong",
        }).encode("utf-8")
        with patch.object(server, "hunyuan_api_request", return_value={
            "Status": "DONE",
            "ResultFile3Ds": [
                {"Type": "GLB", "Url": "https://models.example/result.zip"},
                {"Type": "GLB", "Url": "javascript:alert(1)"},
            ],
        }) as outbound:
            result = lambda_handler.handler(
                event(
                    body,
                    path=server.HUNYUAN_QUERY_PATH,
                    content_type="application/json",
                ),
                None,
            )
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"])["data"], {
            "job_id": "job-123",
            "status": "DONE",
            "files": [{"url": "https://models.example/result.zip", "type": "GLB"}],
        })
        self.assertEqual(outbound.call_args.args[0], "QueryHunyuanTo3DRapidJob")

    def test_tencent_tc3_request_signs_fixed_endpoint_without_sending_the_secret_key(self):
        request = server.build_hunyuan_request(
            "SubmitHunyuanTo3DRapidJob",
            {"ImageBase64": "dGVzdA==", "ResultFormat": "GLB"},
            "a" * 36,
            "test-tencent-key",
            "ap-hongkong",
            timestamp=1700000000,
        )
        self.assertEqual(request.full_url, "https://hunyuan.intl.tencentcloudapi.com/")
        self.assertEqual(request.get_header("X-tc-action"), "SubmitHunyuanTo3DRapidJob")
        self.assertEqual(request.get_header("X-tc-region"), "ap-hongkong")
        self.assertIn("Credential=" + "a" * 36, request.get_header("Authorization"))
        self.assertNotIn("test-tencent-key", request.get_header("Authorization"))
        self.assertEqual(len(request.get_header("Authorization").rsplit("Signature=", 1)[1]), 64)

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
        with server.RFID_EVENT_LOCK:
            server.RFID_LATEST_DEVICE_EVENT = None

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_local_hunyuan_submit_requires_same_origin_and_routes_to_tencent(self):
        body = hunyuan_multipart()
        with patch.object(server, "hunyuan_api_request", return_value={"JobId": "local-job"}) as outbound:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.port, timeout=5
            )
            connection.request(
                "POST",
                server.HUNYUAN_SUBMIT_PATH,
                body=body,
                headers={
                    "Content-Type": CONTENT_TYPE,
                    "Origin": f"http://127.0.0.1:{self.port}",
                },
            )
            response = connection.getresponse()
            status, result = response.status, json.loads(response.read())
            connection.close()
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["job_id"], "local-job")
        outbound.assert_called_once()

        with patch.object(server, "hunyuan_api_request") as outbound:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.port, timeout=5
            )
            connection.request(
                "POST",
                server.HUNYUAN_SUBMIT_PATH,
                body=body,
                headers={
                    "Content-Type": CONTENT_TYPE,
                    "Origin": "https://attacker.example",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            connection.close()
        outbound.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
