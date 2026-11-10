"""Stage and activate immutable API versions only after the workflow gates pass."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

FUNCTION = "ee3070-reshow-studio-api"
ALIAS = "live"
WEBSITE = "https://dkbznu9f5nh9n.cloudfront.net"
BOOTSTRAP_ERROR = {"error": "API release is not deployed yet"}


def aws(*arguments):
    result = subprocess.run(
        ["aws", "lambda", *arguments, "--region", "us-east-1", "--output", "json"],
        check=True, capture_output=True, text=True, timeout=180,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def package(destination, commit, root=Path(".")):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ("server.py", "lambda_handler.py"):
            bundle.writestr(name, (root / name).read_bytes())
        bundle.writestr("index.py", "from lambda_handler import handler\n")
        bundle.writestr("release.json", json.dumps({"commit": commit}))


def save_state(path, state):
    path.write_text(json.dumps(state))


def public_json(path, body=None):
    url = WEBSITE + path
    headers = {}
    if body is not None:
        headers = {"Origin": WEBSITE, "Content-Type": "multipart/form-data; boundary=reshow-preflight",
                   "x-amz-content-sha256": hashlib.sha256(body).hexdigest()}
    request = Request(url, data=body, headers=headers)
    try:
        reply = urlopen(request, timeout=20)
    except HTTPError as error:
        reply = error
    with reply:
        content_type = reply.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if reply.geturl() != url or content_type != "application/json":
            raise RuntimeError("Public API preflight returned an unexpected route or content type")
        data = reply.read(4097)
        if len(data) > 4096:
            raise RuntimeError("Public API preflight response exceeds its size limit")
        try:
            value = json.loads(data)
        except ValueError as error:
            raise RuntimeError("Public API preflight returned invalid JSON") from error
        if not isinstance(value, dict):
            raise RuntimeError("Public API preflight returned an invalid JSON object")
        return reply.status, value


def check_public_routes(previous_static, previous_alias):
    status, health = public_json("/api/health")
    bootstrap = status == 503 and health == BOOTSTRAP_ERROR and previous_alias == "1"
    if not bootstrap and not (status == 200 and health.get("commit") == previous_static):
        raise RuntimeError("Public API health differs from the current website release")
    # Intentionally malformed, credential-free multipart. The exact validation
    # response proves that Origin, Content-Type, POST bytes and the OAC hash all
    # reached the application; a missing/changed hash has a different error.
    status, result = public_json("/api/technology/generate-exhibit", b"--reshow-preflight--\r\n")
    expected = {"error": "Upload exactly one image, account ID and API token."}
    if bootstrap:
        valid = status == 503 and result == BOOTSTRAP_ERROR
    else:
        valid = status == 400 and result == expected
    if not valid:
        raise RuntimeError("Public API upload route failed its credential-free validation check")


def prepare(path, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise ValueError("Expected a lowercase 40-character commit SHA")
    with urlopen(WEBSITE + "/release.json", timeout=20) as reply:
        previous_static = json.loads(reply.read(4096))["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", previous_static):
        raise ValueError("The current website release is invalid")
    previous = aws("get-alias", "--function-name", FUNCTION, "--name", ALIAS)
    if previous.get("RoutingConfig", {}).get("AdditionalVersionWeights"):
        raise RuntimeError("Weighted API aliases require a separately reviewed deployment")
    check_public_routes(previous_static, previous["FunctionVersion"])
    state = {"previousStatic": previous_static, "previousAlias": previous["FunctionVersion"],
             "previousRevision": previous["RevisionId"], "activated": False, "commit": commit}
    save_state(path, state)
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        archive = folder / "api.zip"
        package(archive, commit)
        published = aws("update-function-code", "--function-name", FUNCTION,
                        "--zip-file", "fileb://" + str(archive), "--publish")
        version = published["Version"]
        aws("wait", "function-updated-v2", "--function-name", FUNCTION)
        event = folder / "health-event.json"
        event.write_text(json.dumps({"rawPath": "/api/health", "requestContext": {"http": {"method": "GET"}}}))
        output = folder / "health-result.json"
        result = aws("invoke", "--function-name", FUNCTION, "--qualifier", version,
                     "--payload", "fileb://" + str(event), str(output))
        health = json.loads(output.read_text())
        if result.get("FunctionError") or health.get("statusCode") != 200 or json.loads(health["body"]).get("commit") != commit:
            raise RuntimeError("Staged API version failed its release health check")
    state["version"] = version
    save_state(path, state)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("previous-static=" + previous_static + "\n")


def activate(path):
    state = json.loads(path.read_text())
    # Record the pending change first so cancellation/failure can still roll back.
    state["activated"] = True
    save_state(path, state)
    aws("update-alias", "--function-name", FUNCTION, "--name", ALIAS,
        "--function-version", state["version"], "--revision-id", state["previousRevision"])


def rollback(path):
    if not path.exists():
        return
    state = json.loads(path.read_text())
    if not state.get("activated"):
        return
    current = aws("get-alias", "--function-name", FUNCTION, "--name", ALIAS)
    if current["FunctionVersion"] == state["previousAlias"]:
        return
    if current["FunctionVersion"] != state["version"]:
        raise RuntimeError("API alias changed outside this deployment; refusing to overwrite it")
    aws("update-alias", "--function-name", FUNCTION, "--name", ALIAS,
        "--function-version", state["previousAlias"], "--revision-id", current["RevisionId"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "activate", "rollback"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--commit")
    arguments = parser.parse_args()
    if arguments.action == "prepare":
        prepare(arguments.state, arguments.commit)
    else:
        {"activate": activate, "rollback": rollback}[arguments.action](arguments.state)


if __name__ == "__main__":
    main()
