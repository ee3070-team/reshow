"""Ephemeral local SonarQube setup and durable reports; standard library only."""
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.parse
import urllib.request
import base64

HOST = 'http://127.0.0.1:9000'
PROJECT = 'reshow'
GATE_NAME = 'ReShow overall zero issues'
GATE_METRICS = ('bugs', 'vulnerabilities', 'code_smells')


def api(path, parameters=None, credentials=None):
    headers = {}
    if credentials:
        headers['Authorization'] = 'Basic ' + base64.b64encode(credentials.encode()).decode()
    body = urllib.parse.urlencode(parameters).encode() if parameters is not None else None
    with urllib.request.urlopen(urllib.request.Request(HOST + '/api/' + path, data=body, headers=headers), timeout=30) as response:
        value = response.read()
    return json.loads(value) if value else {}


def bootstrap():
    for attempt in range(120):
        try:
            if api('system/status').get('status') == 'UP':
                break
        except OSError:
            pass
        time.sleep(5)
    else:
        raise RuntimeError('SonarQube did not become ready within 10 minutes')
    password = 'Aa1!' + secrets.token_urlsafe(32)
    print('::add-mask::' + password, flush=True)
    api('users/change_password', {'login': 'admin', 'previousPassword': 'admin', 'password': password}, 'admin:admin')
    credentials = 'admin:' + password
    api('projects/create', {'project': PROJECT, 'name': 'ReShow EE3070'}, credentials)
    configure_gate(credentials)
    token = api('user_tokens/generate', {'name': 'github-run'}, credentials)['token']
    print('::add-mask::' + token, flush=True)
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.write('SONAR_TOKEN=' + token + '\n')


def configure_gate(credentials):
    api('qualitygates/create', {'name': GATE_NAME}, credentials)
    for metric in GATE_METRICS:
        api('qualitygates/create_condition', {'gateName': GATE_NAME, 'metric': metric, 'op': 'GT', 'error': '0'}, credentials)
    api('qualitygates/select', {'gateName': GATE_NAME, 'projectKey': PROJECT}, credentials)


def validate_gate(gate):
    status = gate['projectStatus']
    conditions = status.get('conditions', [])
    expected = {metric: ('GT', '0') for metric in GATE_METRICS}
    actual = {condition['metricKey']: (condition.get('comparator'), condition.get('errorThreshold')) for condition in conditions}
    if actual != expected or status.get('ignoredConditions'):
        raise RuntimeError('SonarQube overall gate conditions missing, changed or ignored')
    if status['status'] != 'OK' or any(condition.get('status') != 'OK' for condition in conditions):
        raise RuntimeError('SonarQube quality gate: ' + status['status'])


def report():
    credentials = os.environ['SONAR_TOKEN'] + ':'
    metadata = dict(line.split('=', 1) for line in Path('.scannerwork/report-task.txt').read_text().splitlines() if '=' in line)
    for attempt in range(120):
        task = api('ce/task?' + urllib.parse.urlencode({'id': metadata['ceTaskId']}), credentials=credentials)['task']
        if task['status'] in ('SUCCESS', 'FAILED', 'CANCELED'):
            break
        time.sleep(5)
    else:
        raise RuntimeError('SonarQube analysis processing timed out')
    folder = Path('sonar-report')
    folder.mkdir(exist_ok=True)
    (folder / 'task.json').write_text(json.dumps(task, indent=2))
    if task['status'] != 'SUCCESS':
        raise RuntimeError('SonarQube compute task: ' + task['status'])
    gate = api('qualitygates/project_status?' + urllib.parse.urlencode({'analysisId': task['analysisId']}), credentials=credentials)
    (folder / 'quality-gate.json').write_text(json.dumps(gate, indent=2))
    measures = api('measures/component?' + urllib.parse.urlencode({'component': PROJECT, 'metricKeys': 'bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc,security_hotspots'}), credentials=credentials)
    (folder / 'measures.json').write_text(json.dumps(measures, indent=2))
    issues = []
    for page in range(1, 201):
        result = api('issues/search?' + urllib.parse.urlencode({'componentKeys': PROJECT, 'ps': 500, 'p': page}), credentials=credentials)
        issues.extend(result['issues'])
        if page * 500 >= result['paging']['total']:
            break
    else:
        raise RuntimeError('Issue export exceeds API pagination limit; report is incomplete')
    (folder / 'issues.json').write_text(json.dumps({'issues': issues}, indent=2))
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(f"## SonarQube\n\nQuality gate: **{gate['projectStatus']['status']}**. {len(issues)} issues exported.\n\nDownload the sonar-report artifact for conditions, issues and measures. This disposable server has no historical/new-code baseline; coverage is not fabricated.\n")


def enforce():
    validate_gate(json.loads(Path('sonar-report/quality-gate.json').read_text()))


if __name__ == '__main__':
    {'bootstrap': bootstrap, 'report': report, 'enforce': enforce}[sys.argv[1]]()
