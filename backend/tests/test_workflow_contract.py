"""Validate published examples and actual workflow responses against OpenAPI.

Run with jsonschema installed for this focused documentation gate. The main
backend suite may omit that optional dependency; formal validation is separate.
"""
import copy
import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'tests'))
import app
import analytics
import media_reports
from test_workflows import WorkflowTable
from test_media_reports import S3
try:
    from jsonschema import Draft4Validator, FormatChecker, RefResolver
except ImportError:
    Draft4Validator = None

TOKEN = 'contract-test-token-' + 'x' * 32
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def json_schema(value):
    """Convert OpenAPI 3.0 nullable into equivalent Draft4 type/enum unions."""
    if isinstance(value, list):
        return [json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: json_schema(item) for key, item in value.items() if key != 'nullable'}
    if value.get('nullable'):
        if '$ref' in result:
            return {'anyOf': [result, {'type': 'null'}]}
        if 'type' in result:
            result['type'] = [result['type'], 'null']
        if 'enum' in result:
            result['enum'] = result['enum'] + [None]
    return result


@unittest.skipUnless(Draft4Validator, 'Install jsonschema for the focused contract gate.')
class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / 'backend/openapi.json').read_text())
        self.schema = json_schema(self.spec)
        self.table, self.s3 = WorkflowTable(), S3()
        for context in [patch.dict(os.environ, {'API_TOKEN': TOKEN, 'AUTH_ENFORCED': 'false', 'ASSET_BUCKET': 'private-test-bucket'}),
                        patch.object(app, '_table', self.table), patch.object(media_reports, '_s3', self.s3),
                        patch.object(app, 'now_utc', return_value=NOW)]:
            context.start()
            self.addCleanup(context.stop)

    def validate(self, value, schema):
        Draft4Validator(json_schema(schema), resolver=RefResolver.from_schema(self.schema),
                        format_checker=FormatChecker()).validate(value)

    def request(self, method, template, data=None, path=None, query=None, match=None, expected=200):
        operation = self.spec['paths'][template][method.lower()]
        request_operation = operation
        # Legacy workflow fixture execution is explicitly enabled only in this
        # local harness. The deployed management surface moved to /staff-api;
        # validate the same stored response against that canonical contract.
        if str(expected) not in operation['responses'] and template.startswith('/api/'):
            operation = self.spec['paths']['/staff-api/' + template[len('/api/'):]][method.lower()]
        headers = {'authorization': 'Bearer ' + TOKEN, 'content-type': 'application/json'}
        if match is not None:
            headers['if-match'] = str(match)
        event = {'rawPath': path or template, 'requestContext': {'http': {'method': method}},
                 'headers': headers, 'queryStringParameters': query,
                 'body': json.dumps(data) if data is not None else ''}
        if data is not None:
            self.validate(data, request_operation['requestBody']['content']['application/json']['schema'])
        response = app.lambda_handler(event, None)
        value = json.loads(response['body'])
        self.assertEqual(response['statusCode'], expected, value)
        self.validate(value, operation['responses'][str(expected)]['content']['application/json']['schema'])
        return value

    def example(self, route):
        return copy.deepcopy(self.spec['paths'][route]['post']['requestBody']['content']['application/json']['example'])

    def test_all_published_request_examples_validate_without_ignoring_nullable(self):
        count = 0
        for path, methods in self.spec['paths'].items():
            for method, operation in methods.items():
                content = operation.get('requestBody', {}).get('content', {}).get('application/json', {})
                if 'example' in content:
                    with self.subTest(path=path, method=method):
                        self.validate(content['example'], content['schema'])
                    count += 1
        self.assertGreaterEqual(count, 18)

    def test_complete_openapi_document_is_formally_valid(self):
        from openapi_spec_validator import validate_spec
        validate_spec(self.spec)
        declared = set(self.spec['components']['securitySchemes'])
        for methods in self.spec['paths'].values():
            for operation in methods.values():
                if isinstance(operation, dict) and 'responses' in operation:
                    for security in operation.get('security', self.spec.get('security', [])):
                        self.assertTrue(set(security) <= declared, security)

    def test_actual_management_observation_and_list_responses_follow_schema(self):
        for route in ['/api/exhibit-profiles', '/api/staff', '/api/showcases', '/api/device-status', '/api/tag-scans', '/api/access-attempts']:
            self.request('POST', route, self.example(route), expected=201)
            self.request('POST', route, self.example(route), expected=200)
        self.request('PATCH', '/api/exhibit-profiles/{exhibit_id}', {'name': 'Changed'},
                     path='/api/exhibit-profiles/frontend-exhibit-01', match=1)
        self.request('GET', '/api/exhibit-profiles/{exhibit_id}', path='/api/exhibit-profiles/frontend-exhibit-01')
        self.request('GET', '/api/showcases/{showcase_id}', path='/api/showcases/frontend-case-01')
        for path in ['/api/exhibit-profiles', '/api/staff', '/api/showcases', '/api/commands', '/api/workflow-activity']:
            self.request('GET', path)
        for path in ['/api/device-status', '/api/tag-scans', '/api/access-attempts']:
            self.request('GET', path, query={'device_id': 'frontend-device-01'})

    def test_actual_pending_terminal_and_label_responses_follow_schema(self):
        self.request('POST', '/api/exhibit-profiles', self.example('/api/exhibit-profiles'), expected=201)
        self.request('POST', '/api/showcases', self.example('/api/showcases'), expected=201)
        command = self.example('/api/commands')
        self.request('POST', '/api/commands', command, expected=201)
        self.request('GET', '/api/commands/{command_id}', path='/api/commands/' + command['command_id'])
        receipt = self.example('/api/commands/{command_id}/acknowledgements')
        receipt['recorded_at'] = '2026-10-26T12:00:01Z'
        self.request('POST', '/api/commands/{command_id}/acknowledgements', receipt,
                     path='/api/commands/' + command['command_id'] + '/acknowledgements', expected=201)
        draft = self.spec['paths']['/api/label-drafts/{exhibit_id}']['put']['requestBody']['content']['application/json']['example']
        self.request('PUT', '/api/label-drafts/{exhibit_id}', draft,
                     path='/api/label-drafts/frontend-exhibit-01', match=0, expected=201)
        self.request('GET', '/api/label-drafts/{exhibit_id}', path='/api/label-drafts/frontend-exhibit-01')
        self.request('GET', '/api/showcases/{showcase_id}', path='/api/showcases/frontend-case-01')

    def test_actual_fence_recovery_and_replacement_responses_follow_schema(self):
        self.request('POST', '/api/showcases', self.example('/api/showcases'), expected=201)
        ready = self.example('/api/device-status')
        ready.update(status_id='lighting-ready', recorded_at=app.timestamp(NOW), remote_lighting=True)
        self.request('POST', '/api/device-status', ready, expected=201)
        command = {'command_id': 'recover-contract', 'showcase_id': 'frontend-case-01', 'type': 'lighting', 'payload': {'mode': 'On'}}
        created = self.request('POST', '/api/commands', command, expected=201)['item']
        requested = self.request('POST', '/api/commands/{command_id}/recovery-requests',
                                 {'recovery_id': 'contract-recovery', 'reason': 'Synthetic ambiguous result'},
                                 path='/api/commands/recover-contract/recovery-requests', expected=201)['item']
        self.assertEqual(requested['status'], 'recovery_pending')
        self.assertGreater(requested['recovery']['fence_generation'], created['resource_generation'])
        observation = self.example('/api/device-status')
        observation.update(status_id='contract-recovery-status', recorded_at=app.timestamp(NOW), remote_lighting=True)
        self.request('POST', '/api/device-status', observation, expected=201)
        receipt = {'acknowledgement_id': 'contract-recovery-ack', 'recovery_id': 'contract-recovery',
                   'device_id': 'frontend-device-01', 'recorded_at': app.timestamp(NOW), 'source': 'demo',
                   'fenced_generation': requested['recovery']['fence_generation'], 'observed_status_id': observation['status_id'], 'outcome': 'not_applied'}
        self.request('POST', '/api/commands/{command_id}/recovery-acknowledgements', receipt,
                     path='/api/commands/recover-contract/recovery-acknowledgements', expected=201)
        self.request('POST', '/api/commands/{command_id}/recovery-acknowledgements', receipt,
                     path='/api/commands/recover-contract/recovery-acknowledgements')
        replacement = self.request('POST', '/api/commands', {**command, 'command_id': 'after-recovery'}, expected=201)['item']
        self.assertGreater(replacement['resource_generation'], receipt['fenced_generation'])
        self.request('GET', '/api/commands', query={'status': 'recovered'})

    def test_actual_card_registration_scan_reference_matches_schema(self):
        self.request('POST', '/api/showcases', self.example('/api/showcases'), expected=201)
        person = self.example('/api/staff')
        self.request('POST', '/api/staff', person, expected=201)
        scan = {'scan_id': 'card-contract-scan', 'scan_attempt_id': 'card-contract-attempt', 'device_id': 'frontend-device-01',
                'recorded_at': app.timestamp(NOW), 'source': 'demo', 'scan_type': 'staff_card', 'tag_id': 'CARD-CONTRACT'}
        self.request('POST', '/api/tag-scans', scan, expected=201)
        command = {'command_id': 'card-contract-command', 'showcase_id': 'frontend-case-01', 'type': 'card',
                   'payload': {'staff_id': person['staff_id'], 'card_id': 'CARD-CONTRACT', 'enabled': True,
                               'scan_id': scan['scan_id'], 'scan_attempt_id': scan['scan_attempt_id']}}
        self.request('POST', '/api/commands', command, expected=201)

    def test_actual_saved_report_and_local_date_responses_follow_schema(self):
        with patch.object(analytics, 'read_timeline', return_value=[]):
            self.request('POST', '/api/report-snapshots', {'report_id': 'contract-report',
                         'device_id': 'empty-device', 'start': '2026-10-25T00:00:00Z',
                         'end': '2026-10-25T01:00:00Z', 'sections': ['missing']}, expected=201)
            self.request('GET', '/api/report-snapshots/{report_id}', path='/api/report-snapshots/contract-report')
            self.request('GET', '/api/report-snapshots')
            self.request('GET', '/api/activity', query={'device_id': 'empty-device',
                         'start_date': '2026-10-25', 'end_date': '2026-10-25', 'interval_minutes': '60'})

    def test_private_media_api_responses_do_not_require_binary_in_json(self):
        request = self.example('/api/media/upload-requests')
        # Source PNG stays in the test harness; returned form is not persisted.
        import base64
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==')
        request['size_bytes'] = len(png)
        result = self.request('POST', '/api/media/upload-requests', request, expected=201)
        self.s3.objects[result['upload']['fields']['key']] = {'body': png, 'type': 'image/png'}
        self.request('POST', '/api/media/{media_id}/complete', {}, path='/api/media/frontend-image-01/complete')
        self.request('GET', '/api/media/{media_id}', path='/api/media/frontend-image-01')


class AcceptanceClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('workflows_live', ROOT / 'demo/test_workflows_live.py')
        cls.client_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.client_module)

    def test_live_utility_completes_against_real_handler_with_local_storage(self):
        module, png = self.client_module, self.client_module.PNG

        class CombinedTable(WorkflowTable):
            def query(self, **kwargs):
                if ':device' not in kwargs['ExpressionAttributeValues']:
                    return super().query(**kwargs)
                partition = kwargs['ExpressionAttributeValues'][':device']
                values = sorted([copy.deepcopy(row) for row in self.items.values() if row['PK'] == partition],
                                key=lambda row: row['SK'])
                return {'Items': values}

        table, s3 = CombinedTable(), S3()
        fixture = json.loads((ROOT / 'demo/analytics_fixture.json').read_text())
        kinds = [('exhibits', 'exhibit', 'exhibit_id'), ('events', 'event', 'event_id'),
                 ('proximity-events', 'proximity_event', 'proximity_event_id'), ('display-history', 'display', 'display_id'),
                 ('warnings', 'warning', 'warning_id'), ('coverage', 'coverage', 'coverage_id')]
        for collection, kind, id_key in kinds:
            for body in fixture[collection]:
                clean = app.validate_exhibit(body) if kind == 'exhibit' else analytics.validate_record(app, kind, body)
                row = {**app.record_key(kind, clean[id_key]), 'entity_type': kind, 'data': clean,
                       'received_at': '2026-10-25T01:01:00.000000Z'}
                table.items[row['PK'], row['SK']] = row
                if kind not in ('exhibit', 'event'):
                    timeline = {**row, 'PK': 'DEVICE#' + clean['device_id'],
                                'SK': kind.upper() + '#' + clean['start'] + '#' + clean[id_key]}
                    table.items[timeline['PK'], timeline['SK']] = timeline

        class LocalClient:
            def __init__(self):
                self.responses = []

            def request(self, path, method='GET', payload=None, expected=(200,), headers=None, auth=True):
                parsed = urlsplit(path)
                request_headers = {'content-type': 'application/json'}
                if auth:
                    request_headers['authorization'] = 'Bearer ' + TOKEN
                request_headers.update({key.lower(): value for key, value in (headers or {}).items()})
                event = {'rawPath': parsed.path, 'requestContext': {'http': {'method': method}},
                         'headers': request_headers, 'queryStringParameters': dict(parse_qsl(parsed.query)),
                         'body': json.dumps(payload) if payload is not None else ''}
                response = app.lambda_handler(event, None)
                value = json.loads(response['body']) if 'application/json' in response['headers'].get('content-type', '') else response['body']
                if response['statusCode'] not in expected:
                    raise AssertionError((method, path, response['statusCode'], value))
                self.responses.append({'method': method, 'path': parsed.path, 'http_status': response['statusCode'],
                                       'response': module.redact(value)})
                return response['statusCode'], value

            def upload_png(self, form):
                s3.objects[form['fields']['key']] = {'body': png, 'type': 'image/png'}

            def download_png(self, download):
                values = [row['body'] for key, row in s3.objects.items() if key.startswith('media/final/')]
                return values[0]

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW

        with patch.dict(os.environ, {'API_TOKEN': TOKEN, 'AUTH_ENFORCED': 'false', 'ASSET_BUCKET': 'private-test-bucket'}), \
                patch.object(app, '_table', table), patch.object(media_reports, '_s3', s3), \
                patch.object(app, 'now_utc', return_value=NOW), patch.object(module, 'datetime', Clock):
            result = module.run(LocalClient(), 'localcheck')
        self.assertGreaterEqual(result['check_count'], 30)
        self.assertTrue(all(row['passed'] for row in result['checks']))
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn('temporary-policy', json.dumps(result))

    def test_evidence_drops_s3_capabilities_recursively(self):
        sensitive = {'upload': {'url': 'https://s3.example/?X-Amz-Credential=secret', 'fields': {'policy': 'secret'}},
                     'nested': [{'download': {'url': 'https://s3.example/?X-Amz-Signature=secret'},
                                 'authorization': 'Bearer private'}], 'item': {'media_id': 'image-1', 'status': 'ready'}}
        rendered = json.dumps(self.client_module.redact(sensitive))
        self.assertNotIn('secret', rendered)
        self.assertNotIn('Bearer', rendered)
        self.assertIn('image-1', rendered)

    def test_rejects_invalid_pacing_before_network(self):
        for value in [float('nan'), float('inf'), 0.5]:
            with self.assertRaises(ValueError):
                self.client_module.Client('https://example.invalid', TOKEN, value)


if __name__ == '__main__':
    unittest.main()
