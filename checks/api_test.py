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

import lambda_handler
import server

ORIGIN = "https://dkbznu9f5nh9n.cloudfront.net"
ACCOUNT = "a" * 32
TOKEN = "test-token-that-is-not-a-real-secret"
IMAGE = b"\xff\xd8\xfftest-image"
CONTENT_TYPE = "multipart/form-data; boundary=reshow-test"


def multipart(account=ACCOUNT, token=TOKEN, image=IMAGE, image_type="image/jpeg", extra=b""):
    body = b""
    for name, value in (("account_id", account.encode()), ("api_token", token.encode())):
        body += b'--reshow-test\r\nContent-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n' + value + b"\r\n"
    body += b'--reshow-test\r\nContent-Disposition: form-data; name="image"; filename="image.jpg"\r\nContent-Type: ' + image_type.encode() + b"\r\n\r\n" + image + b"\r\n"
    return body + extra + b"--reshow-test--\r\n"


def event(body=None, **headers):
    body = multipart() if body is None else body
    return {"rawPath": lambda_handler.GENERATE_PATH,
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"origin": ORIGIN, "content-type": CONTENT_TYPE,
                        "x-amz-content-sha256": hashlib.sha256(body).hexdigest(), **headers},
            "isBase64Encoded": True, "body": base64.b64encode(body).decode()}


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


if __name__ == "__main__":
    unittest.main()
