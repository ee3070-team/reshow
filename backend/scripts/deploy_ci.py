"""Deploy one verified package and restore the previous code if smoke checks fail."""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from zipfile import ZipFile


FUNCTION = 'ee3070-reshow-demo-api'
API = 'https://urauw4ckw1.execute-api.us-east-1.amazonaws.com'


def aws(operation, *arguments):
    result = subprocess.run(
        ['aws', 'lambda', operation, '--function-name', FUNCTION, '--region', 'us-east-1',
         '--no-cli-pager', '--output', 'json', *arguments],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if result.returncode:
        # AWS errors and function metadata can contain sensitive values.
        raise RuntimeError(f'AWS Lambda {operation} failed')
    return json.loads(result.stdout)


def function():
    return aws(
        'get-function', '--query',
        '{sha:Configuration.CodeSha256,revision:Configuration.RevisionId,'
        'runtime:Configuration.Runtime,architecture:Configuration.Architectures[0],'
        'state:Configuration.State,update:Configuration.LastUpdateStatus,url:Code.Location}',
    )


def digest(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode('ascii')


def download(url):
    with urlopen(url, timeout=25) as response:
        return response.read()


def update(package, revision):
    aws('update-function-code', '--revision-id', revision, '--zip-file',
        'fileb://' + str(package.resolve()), '--query', '{sha:CodeSha256}')


def settled():
    for _ in range(24):
        current = function()
        if current['update'] != 'InProgress':
            return current
        time.sleep(5)
    raise RuntimeError('Lambda update did not settle within two minutes')


def verify(expected_sha, contract):
    current = settled()
    if current['sha'] != expected_sha or current['state'] != 'Active' or current['update'] != 'Successful':
        raise RuntimeError('Lambda is not serving the expected code package')
    for attempt in range(5):
        try:
            health = json.loads(download(API + '/api/health'))
            deployed_contract = json.loads(download(API + '/openapi.json'))
            if health['status'] == 'ok' and health['service'] == 'reshow-demo-api' and deployed_contract == contract:
                return
        except (OSError, ValueError, KeyError):
            pass
        if attempt < 4:
            time.sleep(5)
    raise RuntimeError('Public health or OpenAPI verification failed')


def openapi(package):
    with ZipFile(package) as archive:
        return json.loads(archive.read('openapi.json'))


def deploy(candidate, expected_hash):
    data = candidate.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise RuntimeError('Build artifact failed its SHA-256 check')
    expected_sha = digest(data)
    contract = openapi(candidate)
    previous = settled()
    if (previous['runtime'], previous['architecture'], previous['state']) != ('python3.13', 'arm64', 'Active'):
        raise RuntimeError('Production Lambda configuration does not match the artifact')
    if previous['sha'] == expected_sha:
        verify(expected_sha, contract)
        print('Production already has the verified package.')
        return
    with tempfile.TemporaryDirectory() as directory:
        backup = Path(directory) / 'previous.zip'
        try:
            backup.write_bytes(download(previous['url']))
        except OSError:
            raise RuntimeError('Could not download the previous Lambda package') from None
        if digest(backup.read_bytes()) != previous['sha']:
            raise RuntimeError('Previous Lambda package failed its SHA-256 check')
        try:
            update(candidate, previous['revision'])
            verify(expected_sha, contract)
        except Exception:
            current = settled()
            if current['sha'] == expected_sha:
                update(backup, current['revision'])
                verify(previous['sha'], openapi(backup))
                print('The production update failed verification. The previous package was restored.')
            elif current['sha'] != previous['sha']:
                raise RuntimeError('The live revision changed concurrently. It was left intact.') from None
            raise RuntimeError('Production deployment did not complete') from None
    print('Production Lambda and public API verified.')


if __name__ == '__main__':
    archive_path = Path(sys.argv[1])
    checksum = archive_path.with_suffix('.sha256').read_text().strip()
    deploy(archive_path, checksum)
