#!/usr/bin/env python3
"""Isolated synthetic v0.3 acceptance run; no physical hardware claims.

Standard library only. Creates named demo records sequentially (one object per
request), never resets data or auto-acknowledges commands. The explicit synthetic
acknowledgements below are producer assertions. Use a fresh --run-id for a new
run; reuse unchanged IDs only to recover an interrupted request. API credentials
come from environment or the separately supplied private access file and are
never written to evidence. Temporary S3 forms/download URLs stay in memory.
"""
import argparse
import base64
import copy
import csv
import io
import json
import math
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, token, interval=1.1):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('API base URL must be HTTPS without credentials, query or fragment.')
        if len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError('API token must have at least 32 characters without whitespace.')
        if not math.isfinite(interval) or interval < 1:
            raise ValueError('Request interval must be finite and at least one second.')
        self.base_url, self.token, self.interval = base_url.rstrip('/'), token, interval
        self.last_request, self.opener = 0, build_opener(NoRedirect())
        self.responses = []

    def request(self, path, method='GET', payload=None, expected=(200,), headers=None, auth=True):
        data = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        request_headers = {'Accept': 'application/json, text/csv'}
        if auth:
            request_headers['Authorization'] = 'Bearer ' + self.token
        if data is not None:
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        request = Request(self.base_url + path, data=data, headers=request_headers, method=method)
        for attempt in range(4):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with self.opener.open(request, timeout=30) as result:
                    status, content_type = result.status, result.headers.get('Content-Type', '')
                    raw = result.read(4_100_000).decode('utf-8')
            except HTTPError as exc:
                status, content_type = exc.code, exc.headers.get('Content-Type', '')
                raw = exc.read(4_100_000).decode('utf-8')
                if status in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
            except (URLError, TimeoutError, OSError):
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError('HTTPS request failed; retry unchanged IDs/payloads.') from None
            if status not in expected:
                raise RuntimeError('Unexpected HTTP %s for %s %s.' % (status, method, path.split('?')[0]))
            if 'application/json' in content_type:
                value = json.loads(raw)
            elif 'text/csv' in content_type:
                value = raw
            else:
                raise RuntimeError('Unexpected API content type.')
            self.responses.append({'method': method, 'path': path.split('?')[0], 'http_status': status,
                                   'response': redact(value)})
            return status, value
        raise RuntimeError('HTTPS retries exhausted.')

    @staticmethod
    def s3_url(url):
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
                or not (host.endswith('.s3.amazonaws.com') or '.s3.' in host and host.endswith('.amazonaws.com'))):
            raise ValueError('Expected a returned AWS S3 HTTPS URL.')
        return url

    def upload_png(self, form):
        boundary = 'reshow-' + uuid.uuid4().hex
        parts = []
        for key, value in form['fields'].items():
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', key) or '\r' in value or '\n' in value:
                raise RuntimeError('Unexpected upload form field.')
            parts.extend([('--' + boundary + '\r\n').encode(),
                          ('Content-Disposition: form-data; name="' + key + '"\r\n\r\n').encode(),
                          value.encode(), b'\r\n'])
        parts.extend([('--' + boundary + '\r\n').encode(),
                      b'Content-Disposition: form-data; name="file"; filename="demo.png"\r\n',
                      b'Content-Type: image/png\r\n\r\n', PNG, b'\r\n',
                      ('--' + boundary + '--\r\n').encode()])
        request = Request(self.s3_url(form['url']), data=b''.join(parts), method='POST',
                          headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        try:
            with self.opener.open(request, timeout=30) as result:
                if result.status not in (200, 201, 204):
                    raise RuntimeError('S3 upload did not succeed.')
        except (HTTPError, URLError, TimeoutError, OSError):
            raise RuntimeError('Private S3 image upload failed; no capability URL was logged.') from None

    def download_png(self, download):
        request = Request(self.s3_url(download['url']), method='GET')
        try:
            with self.opener.open(request, timeout=30) as result:
                return result.read(1048577)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise RuntimeError('Private S3 image download failed; no capability URL was logged.') from None


def redact(value):
    """Keep metadata and omit temporary capabilities, policy, keys and credentials."""
    if isinstance(value, dict):
        return {key: ('<temporary capability omitted>' if key in ('upload', 'download') else redact(item))
                for key, item in value.items()
                if key.lower() not in ('authorization', 'token', 'bearer_token', 'policy', 'x-amz-credential',
                                       'x-amz-signature', 'x-amz-security-token')}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and ('X-Amz-' in value or 'x-amz-' in value or '?AWSAccessKeyId=' in value):
        return '<temporary capability omitted>'
    return value


def config(access_file):
    base_url, token = os.environ.get('API_BASE_URL', ''), os.environ.get('API_TOKEN', '')
    if base_url or token:
        if not base_url or not token:
            raise ValueError('Set API_BASE_URL and API_TOKEN together.')
        return base_url, token
    if not access_file.is_file():
        raise ValueError('Supply --access-file or API_BASE_URL/API_TOKEN.')
    values = {}
    for line in access_file.read_text().splitlines():
        key, separator, value = line.partition(':')
        if separator:
            values[key.strip().lower()] = value.strip()
    return values.get('api base url', ''), values.get('bearer token', '')


def run(client, run_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,11}', run_id):
        raise ValueError('--run-id requires 1–12 identifier characters.')
    prefix = 'wf-' + run_id
    ids = {key: prefix + '-' + key for key in ('ex1', 'ex2', 'case', 'device', 'staff1', 'staff2', 'image', 'report')}
    tag, card = 'EX-' + run_id.upper() + '-01', 'CARD-' + run_id.upper() + '-01'
    checks = []

    def check(label, predicate):
        if not predicate:
            raise RuntimeError('Verification failed: ' + label)
        checks.append({'check': label, 'passed': True})

    def item(path):
        return client.request(path)[1]['item']

    def create(path, payload):
        return client.request(path, 'POST', payload, expected=(200, 201))[1]['item']

    def exact_member(path, key, wanted, query=None):
        cursor = None
        for _ in range(200):
            params = dict(query or {}, limit=1)
            if cursor:
                params['cursor'] = cursor
            result = client.request(path + '?' + urlencode(params))[1]
            if any(row.get(key) == wanted for row in result['items']):
                return True
            cursor = result.get('next_cursor')
            if not cursor:
                return False
        raise RuntimeError('Acceptance pagination exceeded 200 bounded pages.')

    media = {'media_id': ids['image'], 'content_type': 'image/png', 'size_bytes': len(PNG)}
    _, upload = client.request('/api/media/upload-requests', 'POST', media, expected=(200, 201))
    if upload['item']['status'] == 'pending':
        client.request('/api/media/' + ids['image'] + '/complete', 'POST', {}, expected=(409,))
        check('private image starts pending', 'upload' in upload)
        client.upload_png(upload['upload'])
    ready = client.request('/api/media/' + ids['image'] + '/complete', 'POST', {})[1]['item']
    check('private image verified and ready', ready['status'] == 'ready' and len(ready['sha256']) == 64)
    download = client.request('/api/media/' + ids['image'])[1]
    check('private image exact bytes', client.download_png(download['download']) == PNG)
    _, retry = client.request('/api/media/upload-requests', 'POST', media, expected=(200,))
    check('ready image retry has no new upload form', retry['item']['status'] == 'ready' and 'upload' not in retry)
    client.request('/api/media/upload-requests', 'POST', dict(media, size_bytes=len(PNG) + 1), expected=(409,))
    profile = {'exhibit_id': ids['ex1'], 'name': 'Synthetic workflow exhibit', 'description': 'API acceptance only',
               'tag_id': tag, 'temperature_min_c': 18, 'temperature_max_c': 25,
               'brightness_min_percent': 10, 'brightness_max_percent': 70, 'warning_seconds': 30,
               'collection': 'Workflow acceptance', 'image_media_id': ids['image']}
    first = create('/api/exhibit-profiles', profile)
    _, duplicate = client.request('/api/exhibit-profiles', 'POST', profile, expected=(200,))
    check('identical exhibit ID retry preserves one logical item', duplicate['item'] == first)
    client.request('/api/exhibit-profiles', 'POST', dict(profile, name='conflicting'), expected=(409,))
    second_profile = dict(profile, exhibit_id=ids['ex2'], tag_id=tag + '-02', name='Second synthetic exhibit')
    create('/api/exhibit-profiles', second_profile)
    check('unique tag lookup', item('/api/exhibit-profiles/by-tag/' + quote(tag.lower(), safe=''))['exhibit_id'] == ids['ex1'])
    check('catalog cursor pagination', exact_member('/api/exhibit-profiles', 'exhibit_id', ids['ex2']))
    updated = client.request('/api/exhibit-profiles/' + ids['ex1'], 'PATCH', {'name': 'Versioned synthetic exhibit'},
                             headers={'If-Match': str(first['version'])})[1]['item']
    client.request('/api/exhibit-profiles/' + ids['ex1'], 'PATCH', {'name': 'stale overwrite'},
                   headers={'If-Match': str(first['version'])}, expected=(409,))
    check('stale version preserves updated profile', item('/api/exhibit-profiles/' + ids['ex1']) == updated)
    for number in (1, 2):
        create('/api/staff', {'staff_id': ids['staff' + str(number)], 'name': 'Synthetic operator ' + str(number),
                              'email': prefix + '-' + str(number) + '@example.invalid', 'role': 'Operator'})
    create('/api/showcases', {'showcase_id': ids['case'], 'name': 'Synthetic showcase', 'device_id': ids['device']})
    showcase_path = '/api/showcases/' + ids['case']
    before = item(showcase_path)
    check('unobserved device measurements remain unknown', before['device_status'] is None
          and before['freshness']['measurements_stale'] is None and before.get('confirmed_exhibit_id') is None)

    def command(suffix, kind, payload, expected=(200, 201)):
        body = {'command_id': prefix + '-' + suffix, 'showcase_id': ids['case'], 'type': kind,
                'payload': payload, 'actor': 'Synthetic acceptance client'}
        return client.request('/api/commands', 'POST', body, expected=expected)[1].get('item')

    def ack(cmd, suffix, success, device=None, expected=(200, 201)):
        body = {'acknowledgement_id': prefix + '-' + suffix, 'device_id': device or ids['device'],
                'recorded_at': datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
                'source': 'demo', 'success': success, 'message': 'Synthetic producer receipt, no physical action claimed.'}
        return client.request('/api/commands/' + cmd['command_id'] + '/acknowledgements',
                              'POST', body, expected=expected)[1]

    assignment = command('assignment', 'assignment', {'exhibit_id': ids['ex1']})
    check('assignment stays pending before acknowledgement', assignment['status'] == 'pending'
          and item(showcase_path).get('confirmed_exhibit_id') is None)
    ack(assignment, 'wrong-ack', True, device=prefix + '-wrong-device', expected=(409,))
    check('wrong device cannot alter confirmed assignment', item(showcase_path).get('confirmed_exhibit_id') is None)
    ack(assignment, 'assignment-ack', True)
    check('correct synthetic acknowledgement confirms assignment', item(showcase_path)['confirmed_exhibit_id'] == ids['ex1'])
    label_path = '/api/label-drafts/' + ids['ex1']
    draft = {'title': 'Synthetic label', 'collection': 'Demo', 'description': 'Saved only', 'image_media_id': ids['image']}
    _, saved = client.request(label_path, 'PUT', draft, headers={'If-Match': '0'}, expected=(200, 201))
    draft_item = saved['item']
    client.request(label_path, 'PUT', dict(draft, title='stale'), headers={'If-Match': '0'}, expected=(409,))
    label_command = command('label', 'label', {'exhibit_id': ids['ex1'], 'draft_version': draft_item['version']})
    check('label request stays pending', label_command['status'] == 'pending' and item(showcase_path).get('confirmed_label') is None)
    ack(label_command, 'label-ack', True)
    check('label acknowledgement applies saved content', item(showcase_path)['confirmed_label']['content']['title'] == draft['title'])
    lighting_before = item(showcase_path).get('lighting_mode')
    lighting = command('lighting-failed', 'lighting', {'mode': 'On'})
    check('lighting request preserves confirmed mode while pending', item(showcase_path).get('lighting_mode') == lighting_before)
    ack(lighting, 'lighting-failed-ack', False)
    check('failed lighting acknowledgement preserves confirmed mode', item(showcase_path).get('lighting_mode') == lighting_before
          and item('/api/commands/' + lighting['command_id'])['status'] == 'failed')
    registration = command('card', 'card', {'staff_id': ids['staff1'], 'card_id': card, 'enabled': True})
    check('pending card leaves confirmed staff unchanged', item('/api/staff/' + ids['staff1'])['card_enabled'] is False)
    command('reserved-card', 'card', {'staff_id': ids['staff2'], 'card_id': card, 'enabled': True}, expected=(409,))
    ack(registration, 'card-ack', True)
    check('card confirmed after explicit receipt', item('/api/staff/' + ids['staff1'])['card_enabled'] is True)
    command('duplicate-card', 'card', {'staff_id': ids['staff2'], 'card_id': card, 'enabled': True}, expected=(409,))
    disable = command('card-disable-failed', 'card', {'staff_id': ids['staff1'], 'card_id': card, 'enabled': False})
    ack(disable, 'card-disable-failed-ack', False)
    check('failed card change preserves confirmed access', item('/api/staff/' + ids['staff1'])['card_enabled'] is True)
    old_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec='seconds').replace('+00:00', 'Z')
    status = {'status_id': prefix + '-status', 'device_id': ids['device'], 'recorded_at': old_time,
              'source': 'demo', 'temperature_c': 22, 'brightness_percent': 45, 'occupied': False,
              'distance_cm': None, 'lighting_mode': None, 'lighting_output': None, 'latch': None, 'local_control': None}
    create('/api/device-status', status)
    freshness = item(showcase_path)['freshness']
    check('fresh contact does not freshen old measurement', freshness['measurements_stale'] is True
          and freshness['contact_stale'] is False and freshness['measurement_age_seconds'] >= 86400)
    scan = {'scan_id': prefix + '-scan', 'device_id': ids['device'], 'recorded_at': old_time,
            'source': 'demo', 'scan_type': 'exhibit', 'tag_id': tag}
    create('/api/tag-scans', scan)
    check('uploaded scan can be retrieved', exact_member('/api/tag-scans', 'scan_id', scan['scan_id'], {'device_id': ids['device']}))
    access = {'attempt_id': prefix + '-attempt', 'device_id': ids['device'], 'recorded_at': old_time,
              'source': 'demo', 'card_id': card, 'decision': 'denied', 'reason': 'Synthetic assertion only'}
    create('/api/access-attempts', access)
    check('producer access-attempt history', exact_member('/api/access-attempts', 'attempt_id', access['attempt_id'], {'device_id': ids['device']}))
    check('workflow request and receipt history', bool(client.request('/api/workflow-activity?' + urlencode({'device_id': ids['device'], 'limit': 100}))[1]['items']))
    # Reuse the separate, already verified synthetic analytics fixture. No inferred
    # display intervals or completeness assertions are generated from commands.
    _, report = client.request('/api/reports/event?event_id=analytics-event-01')
    check('existing report keeps partial/missing semantics', report['summary']['totals_interpretation'] == 'recorded_lower_bounds'
          and {b['status'] for b in report['buckets']} >= {'missing', 'partial'})
    snapshot_request = {'report_id': ids['report'], 'title': 'Synthetic workflow snapshot',
                        'event_id': 'analytics-event-01', 'sections': ['activity', 'history', 'environment', 'missing']}
    snapshot = create('/api/report-snapshots', snapshot_request)
    snapshot_path = '/api/report-snapshots/' + ids['report']
    frozen = client.request(snapshot_path)[1]
    client.request('/api/report-snapshots', 'POST', snapshot_request, expected=(200,))
    client.request('/api/report-snapshots', 'POST', dict(snapshot_request, title='conflict'), expected=(409,))
    check('saved report immutable retry and conflict', client.request(snapshot_path)[1] == frozen)
    check('saved report context is not historical attestation', frozen['profile_context']['historical_settings_known'] is False)
    check('snapshot preserves known lower bounds', frozen['report']['summary']['totals_interpretation'] == 'recorded_lower_bounds')
    _, csv_report = client.request(snapshot_path + '?format=csv')
    check('saved report has CSV export', bool(list(csv.DictReader(io.StringIO(csv_report)))))
    check('saved report list uses metadata pagination', exact_member('/api/report-snapshots', 'report_id', ids['report']))
    _, calendar = client.request('/api/activity?' + urlencode({'device_id': 'analytics-demo-01', 'start_date': '2026-10-25', 'end_date': '2026-10-25', 'interval_minutes': 60}))
    check('inclusive local date adapter retains completeness', 'date_selection' in calendar
          and calendar['summary']['totals_interpretation'] == 'recorded_lower_bounds')
    client.request('/api/exhibit-profiles', auth=False, expected=(401,))
    return {'version': '0.3.0', 'run_id': run_id, 'data_kind': 'synthetic_engineering_acceptance',
            'hardware_connected': False, 'ids': ids, 'checks': checks, 'check_count': len(checks),
            'requests': client.responses,
            'limitations': ['Shared bearer, descriptive staff roles; no individual login.',
                            'Demo acknowledgements are producer assertions, not physical proof.',
                            'No reset, automatic ACK, physical unlock or invented coverage was performed.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--access-file', type=Path, default=Path(__file__).with_name('EE3070-ReShow-API-Access.txt'))
    parser.add_argument('--run-id', required=True, help='Fresh isolated 1–12 character identifier suffix.')
    parser.add_argument('--interval', type=float, default=1.1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        client = Client(*config(args.access_file), interval=args.interval)
        result = run(client, args.run_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(redact(result), indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({'passed': True, 'check_count': result['check_count'], 'run_id': args.run_id,
                          'output': str(args.output), 'hardware_connected': False}))
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        # All network error messages are controlled and never include request
        # headers, signed capability URLs, policies or the shared token.
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
