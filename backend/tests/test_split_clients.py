"""Offline client contract checks; no AWS calls or credentials required."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

DEMO = Path(__file__).resolve().parents[1] / "demo"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DEMO / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upload = load("upload_data")
report = load("get_report")


class FakeClient:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"summary": {"count": 2}}

    def request(self, path, method, payload=None, expected=(200,)):
        self.calls.append((path, method, payload))
        return (201, {"status": "created"}) if method == "POST" else (200, self.result)


class ClientTests(unittest.TestCase):
    def test_distinct_ids_dedup_and_coverage_last(self):
        data = {"coverage": [{"coverage_id": "c"}], "exhibits": [
            {"exhibit_id": "a"}, {"exhibit_id": "a"}, {"exhibit_id": "b"}],
            "events": [{"event_id": "e"}]}
        rows, skipped = upload.prepare_uploads(data)
        fake = FakeClient()
        result = upload.run(fake, rows, skipped)
        self.assertEqual(result["created"], 4)
        self.assertEqual(result["skipped_identical_input_duplicates"], 1)
        self.assertEqual([x[:2] for x in fake.calls], [
            ("/api/exhibits", "POST"), ("/api/exhibits", "POST"),
            ("/api/events", "POST"), ("/api/coverage", "POST")])

    def test_late_conflict_and_invalid_record_make_zero_requests(self):
        for data in ({"exhibits": [{"exhibit_id": "a"}], "coverage": [
                {"coverage_id": "c", "value": 1}, {"coverage_id": "c", "value": 2}]},
                {"exhibits": [{"exhibit_id": "a"}], "coverage": [{}]},
                {"exhibits": [{"exhibit_id": ""}]},
                {"exhibits": [3]}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/"input.json"
                path.write_text(json.dumps(data))
                with patch.object(upload, "config", return_value=("https://example.test", "x"*32)) as config, patch.object(upload, "Client", return_value=FakeClient()) as client, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(upload.main(["--input", str(path)]), 1)
                    config.assert_not_called()
                    client.assert_not_called()

    def test_conflicting_duplicate_payload_is_rejected(self):
        data = {"exhibits": [{"exhibit_id": "a"}], "coverage": [
            {"coverage_id": "c", "value": 1}, {"coverage_id": "c", "value": 2}]}
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate ID"):
            upload.prepare_uploads(data)

    def test_unknown_grouped_collection_rejected_before_http(self):
        data = {"exhibits": [{"exhibit_id": "a"}], "warning": [{"warning_id": "w"}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_text(json.dumps(data))
            with patch.object(upload, "config", return_value=("https://example.test", "x"*32)) as config, patch.object(upload, "Client", return_value=FakeClient()) as client, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(upload.main(["--input", str(path)]), 1)
                config.assert_not_called()
                client.assert_not_called()
        fixture = json.loads((DEMO / "analytics_fixture.json").read_text())
        rows, skipped = upload.prepare_uploads(fixture)
        self.assertEqual(len(rows), 11)
        self.assertEqual(skipped, 0)

    def test_array_and_run_id_preserve_input(self):
        data = [{"reading_id": "r", "exhibit_id": "a", "device_id": "d", "source": "demo"}]
        rows, skipped = upload.prepare_uploads(data, "readings", "test01")
        self.assertEqual(rows[0][1]["exhibit_id"], "a-test01")
        self.assertEqual(rows[0][1]["source"], "demo")
        self.assertEqual(data[0]["reading_id"], "r")
        self.assertEqual(skipped, 0)

    def test_report_selection_get_only_json_csv_and_files(self):
        for argv, result in (([], {"display_history": [{"exhibit_id": "a"}, {"exhibit_id": "b"}]}),
                (["--device-id", "d", "--start", "2026-10-25T00:00:00Z", "--end", "2026-10-25T01:00:00Z", "--format", "csv"], "section,id\nsummary,a\n")):
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder)/"report.txt"
                fake = FakeClient(result)
                stdout = io.StringIO()
                with patch.object(report, "config", return_value=("https://example.test", "x"*32)), patch.object(report, "Client", return_value=fake), contextlib.redirect_stdout(stdout):
                    self.assertEqual(report.main(argv+["--output", str(output)]), 0)
                self.assertEqual(output.read_text(), stdout.getvalue())
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(fake.calls[0][1:], ("GET", None))
                self.assertTrue(fake.calls[0][0].startswith("/api/reports/event?"))
                if isinstance(result, dict):
                    self.assertEqual(json.loads(stdout.getvalue()), result)
                    self.assertIn("event_id=analytics-event-01", fake.calls[0][0])
                else:
                    self.assertEqual(stdout.getvalue(), result)
                    self.assertNotIn("event_id=", fake.calls[0][0])

    def test_report_bad_selection_no_http(self):
        for args in (["--device-id", "d"], ["--event-id", "e", "--device-id", "d", "--start", "s", "--end", "e"]):
            with patch.object(report, "config", return_value=("https://example.test", "x"*32)) as config, patch.object(report, "Client", return_value=FakeClient()) as client, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(report.main(args), 1)
                config.assert_not_called()
                client.assert_not_called()

    def test_config_file_env_and_partial_env(self):
        for module in (upload, report):
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
                path = Path(folder)/"access.txt"
                path.write_text("API Base URL: https://example.test\nBearer Token: "+"x"*32+"\n")
                self.assertEqual(module.config(path), ("https://example.test", "x"*32))
                with patch.dict(os.environ, {"API_TOKEN": "secret"}):
                    with self.assertRaises(ValueError):
                        module.config(path)

    def test_retry_unchanged_post_and_no_token_in_errors(self):
        token = "private-sensitive-token-0123456789"
        for module in (upload, report):
            client = module.Client("https://example.test", token)
            class Opener:
                calls = []
                def open(self, request, timeout):
                    self.calls.append(request)
                    raise HTTPError(request.full_url, 429, token, {}, io.BytesIO(token.encode()))
            client.opener = Opener()
            client.opener.calls = []
            with patch.object(module.time, "sleep"), self.assertRaises(RuntimeError) as caught:
                client.request("/api/readings", "POST", {"reading_id": "same"})
            self.assertEqual(len(client.opener.calls), 4)
            self.assertEqual(len({r.data for r in client.opener.calls}), 1)
            self.assertNotIn(token, str(caught.exception))

    def test_not_found_does_not_seed(self):
        fake = FakeClient()
        def fail(*args, **kwargs):
            fake.calls.append(args)
            raise RuntimeError("HTTP 404: event not found; upload it first or choose an existing event.")
        fake.request = fail
        stderr = io.StringIO()
        with patch.object(report, "config", return_value=("https://example.test", "x"*32)), patch.object(report, "Client", return_value=fake), contextlib.redirect_stderr(stderr):
            self.assertEqual(report.main([]), 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][1], "GET")
        self.assertIn("upload it first", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
