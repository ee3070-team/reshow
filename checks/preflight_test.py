"""Fail closed when the real runtime archive or deployment transaction is invalid."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load('preflight', 'scripts/preflight.py')
api_deploy = load('api_deploy', 'scripts/api-deploy.py')
deploy = load('static_deploy', 'deploy/reshow-deploy.py')
SHA = 'a' * 40


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as bundle:
        for name, data in files.items():
            entry = tarfile.TarInfo('repo/' + name)
            entry.size = len(data)
            bundle.addfile(entry, io.BytesIO(data))
    return stream.getvalue()


class PreflightTests(unittest.TestCase):
    def test_real_extractor_rejects_an_unsupported_committed_asset_before_deploy(self):
        files = {'index.html': b'home', 'auth/callback.html': b'callback', 'js/app.js': b'code', 'assets/model.glb': b'glTF'}
        with tempfile.TemporaryDirectory() as temporary:
            built = Path(temporary)
            for name, value in files.items():
                target = built / name
                target.parent.mkdir(exist_ok=True, parents=True)
                target.write_bytes(value)
            preflight.check_archive(archive(files), built)
            with self.assertRaisesRegex(ValueError, r'unsupported.bin.*file type'):
                preflight.check_archive(archive({**files, 'assets/unsupported.bin': b'unsafe'}), built)
            with self.assertRaisesRegex(RuntimeError, 'extractor and built website differ'):
                preflight.check_archive(archive({**files, 'js/app.js': b'wrong-code'}), built)

    def test_installed_helper_validation_never_switches_or_restarts_production(self):
        bundle = archive({'index.html': b'home', 'js/app.js': b'code', 'server.py': b'private backend'})
        with patch.object(deploy, 'download_archive', return_value=bundle), patch.object(deploy, 'point_current') as current, patch.object(deploy.subprocess, 'run') as restart:
            deploy.validate(SHA)
            current.assert_not_called()
            restart.assert_not_called()

    def test_api_zip_has_only_backend_files_and_immutable_release_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'api.zip'
            api_deploy.package(output, SHA)
            with zipfile.ZipFile(output) as bundle:
                self.assertEqual(set(bundle.namelist()), {'index.py', 'server.py', 'lambda_handler.py', 'release.json'})
                self.assertEqual(json.loads(bundle.read('release.json')), {'commit': SHA})
        preflight.check_api(SHA)

    def test_alias_activation_and_rollback_use_revision_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state.json'
            state.write_text(json.dumps({'previousAlias': '2', 'previousRevision': 'old-revision', 'version': '3', 'activated': False}))
            with patch.object(api_deploy, 'aws') as command:
                api_deploy.activate(state)
                self.assertIn('old-revision', command.call_args.args)
                self.assertTrue(json.loads(state.read_text())['activated'])
            with patch.object(api_deploy, 'aws', side_effect=[{'FunctionVersion': '3', 'RevisionId': 'new-revision'}, {}]) as command:
                api_deploy.rollback(state)
                self.assertIn('2', command.call_args.args)
                self.assertIn('new-revision', command.call_args.args)
            with patch.object(api_deploy, 'aws', return_value={'FunctionVersion': '99', 'RevisionId': 'another-actor'}) as command:
                with self.assertRaisesRegex(RuntimeError, 'outside this deployment'):
                    api_deploy.rollback(state)
                command.assert_called_once()

    def test_failed_staged_api_health_never_activates_a_version(self):
        def command(*arguments):
            if arguments[0] == 'get-alias':
                return {'FunctionVersion': '1', 'RevisionId': 'prior'}
            if arguments[0] == 'update-function-code':
                return {'Version': '2'}
            if arguments[0] == 'invoke':
                Path(arguments[-1]).write_text(json.dumps({'statusCode': 503, 'body': '{}'}))
                return {'StatusCode': 200}
            return {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(api_deploy, 'urlopen') as fetch, patch.object(api_deploy, 'check_public_routes'), patch.object(api_deploy, 'aws', side_effect=command) as run:
            fetch.return_value.__enter__.return_value.read.return_value = json.dumps({'commit': SHA}).encode()
            state = Path(temporary) / 'state.json'
            with self.assertRaisesRegex(RuntimeError, 'Staged API version failed'):
                api_deploy.prepare(state, SHA)
            self.assertFalse(json.loads(state.read_text())['activated'])
            self.assertNotIn('update-alias', [call.args[0] for call in run.call_args_list])

    def test_public_route_preflight_accepts_matching_release_and_checks_signed_multipart(self):
        replies = [(200, {'commit': SHA}), (400, {'error': 'Upload exactly one image, account ID and API token.'})]

        def fetch(request, timeout):
            self.assertEqual(timeout, 20)
            status, body = replies.pop(0)
            if request.get_method() == 'POST':
                self.assertEqual(request.get_header('Origin'), api_deploy.WEBSITE)
                self.assertEqual(request.get_header('Content-type'), 'multipart/form-data; boundary=reshow-preflight')
                self.assertEqual(request.get_header('X-amz-content-sha256'), api_deploy.hashlib.sha256(request.data).hexdigest())
                self.assertNotIn(b'api_token', request.data)
            response = HTTPError(request.full_url, status, '', {'Content-Type': 'application/json'}, io.BytesIO(json.dumps(body).encode()))
            if status >= 400:
                raise response
            return response
        with patch.object(api_deploy, 'urlopen', side_effect=fetch):
            api_deploy.check_public_routes(SHA, '2')
        self.assertEqual(replies, [])

    def test_bootstrap_exception_is_exact_and_only_allowed_for_the_initial_alias(self):
        with patch.object(api_deploy, 'public_json', return_value=(503, api_deploy.BOOTSTRAP_ERROR)):
            api_deploy.check_public_routes(SHA, '1')
            with self.assertRaisesRegex(RuntimeError, 'Public API health differs'):
                api_deploy.check_public_routes(SHA, '2')
        with patch.object(api_deploy, 'public_json', side_effect=[(503, api_deploy.BOOTSTRAP_ERROR), (503, {'error': 'some other failure'})]):
            with self.assertRaisesRegex(RuntimeError, 'upload route failed'):
                api_deploy.check_public_routes(SHA, '1')

    def test_broken_public_routes_prevent_all_api_mutations(self):
        cases = [
            [(403, {'error': 'denied'})],
            [(404, {'error': 'missing'})],
            [(200, {'commit': 'b' * 40})],
            [(503, {'error': 'unexpected'})],
            [(200, {'commit': SHA}), (403, {'error': 'denied'})],
            [(200, {'commit': SHA}), (400, {'error': 'The image upload checksum is invalid.'})],
        ]
        for replies in cases:
            with self.subTest(replies=replies), tempfile.TemporaryDirectory() as temporary, patch.object(api_deploy, 'urlopen') as fetch, patch.object(api_deploy, 'public_json', side_effect=replies), patch.object(api_deploy, 'aws', return_value={'FunctionVersion': '2', 'RevisionId': 'before'}) as command:
                fetch.return_value.__enter__.return_value.read.return_value = json.dumps({'commit': SHA}).encode()
                state = Path(temporary) / 'state.json'
                with self.assertRaisesRegex(RuntimeError, 'Public API'):
                    api_deploy.prepare(state, SHA)
                self.assertEqual([call.args[0] for call in command.call_args_list], ['get-alias'])
                self.assertFalse(state.exists())

    def test_public_route_reader_rejects_html_oversized_and_invalid_json(self):
        cases = [('text/html', b'<html>error</html>'), ('application/json', b'x' * 4097), ('application/json', b'not-json')]
        for content_type, body in cases:
            with self.subTest(content_type=content_type, length=len(body)), patch.object(api_deploy, 'urlopen') as fetch:
                fetch.return_value = HTTPError(api_deploy.WEBSITE + '/api/health', 200, '', {'Content-Type': content_type}, io.BytesIO(body))
                with self.assertRaisesRegex(RuntimeError, 'Public API preflight'):
                    api_deploy.public_json('/api/health')

    def test_infrastructure_requires_https_iam_oac_and_never_caches_api(self):
        frontend = json.loads(Path('infra/frontend.json').read_text())['Resources']
        config = frontend['FrontendDistribution']['Properties']['DistributionConfig']
        api_origin = next(origin for origin in config['Origins'] if origin['Id'] == 'StudioApi')
        self.assertEqual(api_origin['CustomOriginConfig']['OriginProtocolPolicy'], 'https-only')
        self.assertEqual(api_origin['CustomOriginConfig']['OriginSSLProtocols'], ['TLSv1.2'])
        behavior = next(item for item in config['CacheBehaviors'] if item['PathPattern'] == 'api/*')
        self.assertEqual(behavior['ViewerProtocolPolicy'], 'https-only')
        self.assertIn('POST', behavior['AllowedMethods'])
        # AWS disallows x-amz-content-sha256 in legacy ForwardedValues.Headers.
        # Managed policies disable caching and forward viewer headers except Host
        # so Lambda's URL Host and CloudFront's OAC signature remain consistent.
        self.assertEqual(behavior['CachePolicyId'], '4135ea2d-6df8-44a3-9df3-4b5a84be39ad')
        self.assertEqual(behavior['OriginRequestPolicyId'], 'b689b0a8-53d0-40ab-baf2-68738e2966ac')
        self.assertNotIn('ForwardedValues', behavior)
        self.assertEqual(config['DefaultCacheBehavior']['TargetOriginId'], 'ExistingEC2')
        self.assertEqual(config['DefaultCacheBehavior']['AllowedMethods'], ['GET', 'HEAD'])
        default = config['DefaultCacheBehavior']
        self.assertEqual([default[key] for key in ('MinTTL', 'DefaultTTL', 'MaxTTL')], [0, 0, 0])
        for path in ('auth/callback.html', 'index.html', 'js/*'):
            auth = next(item for item in config['CacheBehaviors'] if item['PathPattern'] == path)
            self.assertEqual(auth['TargetOriginId'], 'ExistingEC2')
            self.assertEqual(auth['CachePolicyId'], '4135ea2d-6df8-44a3-9df3-4b5a84be39ad')
            self.assertNotIn('OriginRequestPolicyId', auth)
            self.assertNotIn('ForwardedValues', auth)
        headers = frontend['FrontendResponseHeaders']['Properties']['ResponseHeadersPolicyConfig']['SecurityHeadersConfig']
        self.assertEqual(headers['ReferrerPolicy'], {'ReferrerPolicy': 'no-referrer', 'Override': True})
        self.assertNotIn('Logging', config)
        studio = json.loads(Path('infra/studio-api.json').read_text())['Resources']
        self.assertEqual(studio['ApiUrl']['Properties']['AuthType'], 'AWS_IAM')
        self.assertEqual(studio['ApiOriginAccessControl']['Properties']['OriginAccessControlConfig']['SigningBehavior'], 'always')
        for name in ('InvokePermission', 'InvokeUrlPermission'):
            permission = studio[name]['Properties']
            self.assertEqual(permission['Principal'], 'cloudfront.amazonaws.com')
            self.assertEqual(permission['SourceArn'], 'arn:aws:cloudfront::835822826851:distribution/E1P2W5YKS25NPQ')
        statements = studio['ApiRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
        self.assertEqual(set(statements[0]['Action']), {'logs:CreateLogStream', 'logs:PutLogEvents'})


if __name__ == '__main__':
    unittest.main()
