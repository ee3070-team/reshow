"""Exercise the real outbound request and blocking decisions without a live gateway."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('review', 'scripts/ai-review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
COMMIT = 'a' * 40
PASS = {'decision': 'pass', 'blockers': [], 'review': 'Deployment evidence is consistent.'}


class ReviewTests(unittest.TestCase):
    def fixture(self, root):
        for name in review.REVIEW_FILES:
            source = root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('public ' + name + '\n' + 'x' * review.MAX_FILE_CHARS)
        folder = root / 'sonar-report'
        folder.mkdir()
        (folder / 'issues.json').write_text(json.dumps({'issues': [{'rule': 'javascript:S123', 'message': 'private-issue-prose', 'component': 'private/path'}] * 101}))
        preflight = {
            'commit': COMMIT, 'status': 'pass',
            'publicRuntime': [{'path': 'index.html', 'bytes': 20}, {'path': 'assets/helmet.glb', 'bytes': 1234567}],
            'contract': {key: 'public ' + key for key in review.CONTRACT_KEYS - {'apiPackage'}},
            'private': 'unlisted-report-secret',
        }
        preflight['contract']['apiPackage'] = ['server.py', 'lambda_handler.py', 'index.py', 'release.json']
        (folder / 'preflight-report.json').write_text(json.dumps(preflight))
        return preflight

    def run_review(self, root, decision=PASS, reason='stop', raw=None):
        content = json.dumps(decision) if raw is None else raw
        response = io.BytesIO(json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': reason}],
                                         'usage': {'completion_tokens': 8192}, 'private': 'raw-response-secret'}).encode())
        environment = {'AI_API_KEY': 'api-key-sentinel', 'PRIVATE_CONTEXT': 'environment-secret', 'GITHUB_SHA': COMMIT}
        with patch.dict(os.environ, environment, clear=True), patch.object(review.urllib.request, 'urlopen', return_value=response) as send:
            review.main(root)
        return send.call_args.args[0]

    def test_request_has_bounded_allowlist_preflight_and_no_private_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / '.env').write_text('unlisted-file-secret')
            (root / 'unknown.py').write_text('unlisted-source-secret')
            (root / 'assets').mkdir()
            (root / 'assets/helmet.glb').write_bytes(b'binary-model-secret')
            request = self.run_review(root)
            self.assertEqual(request.full_url, 'https://18.163.201.242/ica/harry/v1/chat/completions')
            self.assertEqual(request.get_header('Authorization'), 'Bearer api-key-sentinel')
            self.assertEqual(set(dict(request.header_items())), {'Authorization', 'Content-type'})
            payload = json.loads(request.data)
            self.assertEqual(payload['model'], 'claude-sonnet-5-5')
            self.assertEqual(payload['max_tokens'], 8192)
            self.assertEqual(payload['reasoning_effort'], 'low')
            context = json.loads(payload['messages'][1]['content'])
            self.assertEqual([item['path'] for item in context['deploymentSources']], list(review.REVIEW_FILES))
            self.assertIn('server.py', review.REVIEW_FILES)
            self.assertIn('lambda_handler.py', review.REVIEW_FILES)
            self.assertIn('infra/frontend.json', review.REVIEW_FILES)
            self.assertIn('infra/studio-api.json', review.REVIEW_FILES)
            for item in context['deploymentSources']:
                self.assertEqual(len(item['content']), review.MAX_FILE_CHARS)
                self.assertTrue(item['truncated'])
            self.assertEqual(len(context['first100']), 100)
            self.assertEqual(context['preflight']['commit'], COMMIT)
            self.assertEqual(context['preflight']['publicRuntime'][1], {'path': 'assets/helmet.glb', 'bytes': 1234567})
            self.assertEqual(set(context['preflight']['contract']), review.CONTRACT_KEYS)
            for secret in ['api-key-sentinel', 'environment-secret', 'unlisted-file-secret', 'unlisted-source-secret',
                           'binary-model-secret', 'unlisted-report-secret', 'private-issue-prose', 'private/path']:
                self.assertNotIn(secret, request.data.decode())
            self.assertIn('"decision": "pass"', (root / 'sonar-report/ai-review.md').read_text())

    def test_explicit_block_fails_and_preserves_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            decision = {'decision': 'block', 'blockers': ['deploy/reshow-deploy.py: GLB is rejected'], 'review': 'API api-key-sentinel redacted.'}
            with self.assertRaisesRegex(RuntimeError, 'AI explicitly blocked deployment'):
                self.run_review(root, decision)
            artifact = (root / 'sonar-report/ai-review.md').read_text()
            self.assertIn('GLB is rejected', artifact)
            self.assertIn('"decision": "block"', artifact)
            self.assertNotIn('api-key-sentinel', artifact)

    def test_malformed_or_contradictory_decisions_fail_closed(self):
        invalid = ['Markdown review', '```json\n{}\n```', '[]', '{}',
                   json.dumps(dict(PASS, extra='unsupported')),
                   json.dumps(dict(PASS, decision='approve')),
                   json.dumps(dict(PASS, blockers=['contradiction'])),
                   json.dumps(dict(PASS, decision='block')),
                   json.dumps(dict(PASS, blockers='none')),
                   json.dumps(dict(PASS, review='')),
                   json.dumps(dict(PASS, review='x' * 4001)),
                   json.dumps(dict(PASS, decision='block', blockers=[1]))]
        for raw in invalid:
            with self.subTest(raw=raw[:80]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root)
                with self.assertRaisesRegex(RuntimeError, 'AI decision'):
                    self.run_review(root, raw=raw)
                self.assertIn('No AI result was accepted', (root / 'sonar-report/ai-review.md').read_text())

    def test_incomplete_or_empty_responses_fail_with_safe_diagnostics(self):
        for reason, content in [('length', ''), ('length', json.dumps(PASS)), ('content_filter', 'partial-review-secret'),
                                ('stop', ''), ('untrusted-finish-secret', 'partial-review-secret'), (None, json.dumps(PASS))]:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root)
                with self.assertRaisesRegex(RuntimeError, 'completion_tokens=8192; max_tokens=8192'):
                    self.run_review(root, reason=reason, raw=content)
                artifact = (root / 'sonar-report/ai-review.md').read_text()
                self.assertIn('No AI result was accepted', artifact)
                for secret in ('api-key-sentinel', 'partial-review-secret', 'untrusted-finish-secret', 'raw-response-secret'):
                    self.assertNotIn(secret, artifact)

    def test_missing_failed_wrong_commit_or_unsafe_preflight_fails_before_network(self):
        for case in ('missing', 'failed', 'wrong_commit', 'malformed', 'private_path', 'large', 'bad_contract'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                report = self.fixture(root)
                path = root / 'sonar-report/preflight-report.json'
                if case == 'missing':
                    path.unlink()
                elif case == 'malformed':
                    path.write_text('{')
                elif case == 'large':
                    path.write_text('x' * (review.MAX_REPORT_BYTES + 1))
                else:
                    if case == 'failed':
                        report['status'] = 'fail'
                    elif case == 'wrong_commit':
                        report['commit'] = 'b' * 40
                    elif case == 'private_path':
                        report['publicRuntime'].append({'path': '/private/.env', 'bytes': 10})
                    elif case == 'bad_contract':
                        report['contract']['unexpected'] = 'private'
                    path.write_text(json.dumps(report))
                with patch.dict(os.environ, {'AI_API_KEY': 'key', 'GITHUB_SHA': COMMIT}, clear=True), patch.object(review.urllib.request, 'urlopen') as send:
                    with self.assertRaisesRegex(RuntimeError, 'preflight'):
                        review.main(root)
                    send.assert_not_called()
                self.assertIn('blocked', (root / 'sonar-report/ai-review.md').read_text())

    def test_missing_key_fails_without_network(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True), patch.object(review.urllib.request, 'urlopen') as send:
            with self.assertRaisesRegex(RuntimeError, 'AI_API_KEY is not configured'):
                review.main(Path(temporary))
            send.assert_not_called()
            self.assertIn('AI review failed', (Path(temporary) / 'sonar-report/ai-review.md').read_text())

    def test_frontend_does_not_use_ci_key(self):
        for path in [Path('index.html'), *Path('js').rglob('*.js')]:
            self.assertNotIn('AI_API_KEY', path.read_text(), str(path))
            self.assertNotIn('18.163.201.242', path.read_text(), str(path))


if __name__ == '__main__':
    unittest.main()
