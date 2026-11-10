"""Exercise the real deployment extractor and Lambda artifact before any AWS writes."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file() and path.name != 'release.json'}


def check_archive(archive, built):
    deploy = load('deployment', Path('deploy/reshow-deploy.py'))
    if len(archive) > deploy.MAX_ARCHIVE:
        raise ValueError('Archive exceeds the production download size limit')
    with tempfile.TemporaryDirectory() as temporary:
        extracted = Path(temporary)
        deploy.extract_runtime(archive, extracted)
        if hashes(extracted) != hashes(built):
            raise RuntimeError('The production extractor and built website differ')


def working_archive():
    # Local verification of pending edits; CI uses the immutable git archive below.
    names = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard', '-z']).decode().split('\0')
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as bundle:
        for name in sorted(set(names) - {''}):
            path = Path(name)
            if path.is_file():
                bundle.add(path, arcname='reshow/' + name, recursive=False)
    return data.getvalue()


def check_api(commit):
    deployment = load('api_deployment', Path('scripts/api-deploy.py'))
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'api.zip'
        deployment.package(archive, commit)
        with zipfile.ZipFile(archive) as bundle:
            if set(bundle.namelist()) != {'server.py', 'lambda_handler.py', 'index.py', 'release.json'}:
                raise RuntimeError('Unexpected files in the private API package')
            if json.loads(bundle.read('release.json')) != {'commit': commit}:
                raise RuntimeError('API release metadata differs from the website')
            for name in ('server.py', 'lambda_handler.py', 'index.py'):
                compile(bundle.read(name), name, 'exec')
            bundle.extractall(temporary)
        # Import only in an unprivileged CI subprocess, never on the EC2 installer.
        subprocess.run(['python3', '-c',
                        'import index,json; r=index.handler({"rawPath":"/api/health","requestContext":{"http":{"method":"GET"}}},None); assert r["statusCode"]==200; assert json.loads(r["body"])["commit"]=="' + commit + '"'],
                       cwd=temporary, check=True, timeout=10)


def check_vendor():
    root = Path('js/vendor/three-r128')
    manifest = json.loads(Path('deploy/three-r128-manifest.json').read_text())
    expected = {'three.min.js', 'GLTFLoader.js', 'LICENSE.txt'}
    if set(manifest) != expected or {path.name for path in root.iterdir()} != expected:
        raise RuntimeError('Unexpected files in the pinned Three.js package')
    for name, metadata in manifest.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != metadata['sha256']:
            raise RuntimeError('Vendored Three.js file differs from its provenance manifest: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--working-tree', action='store_true')
    arguments = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    archive = working_archive() if arguments.working_tree else subprocess.check_output(['git', 'archive', '--format=tar.gz', '--prefix=reshow/', 'HEAD'])
    check_archive(archive, Path('dist'))
    check_api(commit)
    check_vendor()
    inventory = [{'path': str(path.relative_to('dist')), 'bytes': path.stat().st_size}
                 for path in sorted(Path('dist').rglob('*')) if path.is_file()]
    Path('preflight-report.json').write_text(json.dumps({
        'commit': commit, 'status': 'pass', 'publicRuntime': inventory,
        'contract': {'static': 'EC2 allowlisted files only; no repository Python in public root',
                     'apiFunction': 'ee3070-reshow-studio-api', 'apiAlias': 'live',
                     'apiTransport': 'CloudFront HTTPS -> AWS_IAM Lambda Function URL with OAC',
                     'apiCache': 'disabled; credentials and images are never logged or persisted',
                     'apiPost': 'multipart bytes with x-amz-content-sha256; Origin and Content-Type forwarded',
                     'apiPackage': ['server.py', 'lambda_handler.py', 'index.py', 'release.json']}
    }, indent=2) + '\n')
    print('Deployment preflight passed: real extractor, exact public files and private API package')


if __name__ == '__main__':
    main()
