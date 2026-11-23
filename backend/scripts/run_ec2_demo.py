#!/usr/bin/env python3
"""Install/run the token-free simulator through SSM and retrieve exact-readback evidence."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import time

from deploy import Aws, AwsError, PROJECT, protected_json


MARKER = "EE3070_EVIDENCE_B64:"


def remote_commands(source, region, count):
    encoded = base64.b64encode(source).decode("ascii")
    install = (
        "python3 - <<'EE3070_INSTALL'\n"
        "import base64\nfrom pathlib import Path\n"
        "p = Path('/opt/ee3070-reshow/simulate_upload.py')\n"
        f"p.write_bytes(base64.b64decode('{encoded}'))\np.chmod(0o700)\n"
        "EE3070_INSTALL"
    )
    run = (
        "if ! python3 /opt/ee3070-reshow/simulate_upload.py --ssm-config "
        f"--region {shlex.quote(region)} --count {count} "
        "--save-input /opt/ee3070-reshow/demo-input.json "
        "--output /opt/ee3070-reshow/demo-evidence.json "
        "> /opt/ee3070-reshow/last-run.log 2>&1; then "
        "tail -c 12000 /opt/ee3070-reshow/last-run.log >&2; exit 1; fi"
    )
    retrieve = (
        "python3 - <<'EE3070_EVIDENCE'\n"
        "import base64, json\nfrom pathlib import Path\n"
        "evidence = json.loads(Path('/opt/ee3070-reshow/demo-evidence.json').read_text())\n"
        "raw = json.dumps(evidence, separators=(',', ':'), allow_nan=False).encode()\n"
        f"print('{MARKER}' + base64.b64encode(raw).decode('ascii'))\n"
        "EE3070_EVIDENCE"
    )
    return ["set -eu", "umask 077", "install -d -m 0700 /opt/ee3070-reshow", install, run, retrieve]


def run(args):
    if not 1 <= args.count <= 12:
        raise ValueError("--count must be 1 to 12 so complete evidence fits SSM's output limit.")
    deployment = json.loads(args.deployment_file.read_text())
    region = deployment["region"]
    instance = deployment["InstanceId"]
    aws = Aws(args.profile or deployment.get("profile", "my-aws"), region)
    identity = aws.call("sts", "get-caller-identity", timeout=30)
    if identity.get("Account") != deployment["account_id"]:
        raise ValueError("AWS account does not match deployment.json; no command was sent.")
    source = (PROJECT / "demo" / "simulate_upload.py").read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    deadline = time.monotonic() + 300

    def call(*arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("SSM demo did not finish within 300 seconds; inspect the reported command before retrying.")
        return aws.call(*arguments, timeout=min(30, remaining))

    print(f"Waiting for SSM on {instance}.", flush=True)
    while True:
        response = call("ssm", "describe-instance-information", "--filters", f"Key=InstanceIds,Values={instance}")
        if any(item.get("PingStatus") == "Online" for item in response.get("InstanceInformationList", [])):
            break
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    request = {
        "DocumentName": "AWS-RunShellScript",
        "InstanceIds": [instance],
        "Comment": "EE3070 synthetic-data HTTPS upload and exact database readback verification",
        "TimeoutSeconds": 300,
        "Parameters": {"commands": remote_commands(source, region, args.count), "executionTimeout": ["300"]},
    }
    with tempfile.TemporaryDirectory(prefix="ee3070-ssm-") as working:
        request_file = Path(working) / "ssm-request.json"
        protected_json(request_file, request)
        sent = call("ssm", "send-command", "--cli-input-json", f"file://{request_file}")
    command_id = sent["Command"]["CommandId"]
    print(f"SSM command: {command_id}; waiting for {args.count} verified readings.", flush=True)
    while True:
        try:
            invocation = call("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", instance)
        except AwsError as error:
            if "InvocationDoesNotExist" not in str(error):
                raise
            invocation = {"Status": "Pending"}
        status = invocation["Status"]
        if status == "Success":
            break
        if status not in ("Pending", "InProgress", "Delayed", "Cancelling"):
            detail = invocation.get("StandardErrorContent", "").strip() or invocation.get("StandardOutputContent", "").strip()
            raise RuntimeError(f"SSM command {command_id} ended with {status}: {detail[:12000]}")
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    output = invocation.get("StandardOutputContent", "")
    encoded = next((line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)), None)
    if encoded is None:
        raise RuntimeError(f"SSM command {command_id} returned no complete evidence marker.")
    try:
        evidence = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, json.JSONDecodeError):
        raise RuntimeError(f"SSM evidence was truncated or malformed; retrieve /opt/ee3070-reshow/demo-evidence.json from {instance}.") from None
    records = evidence.get("readings", [])
    if (evidence.get("verified_count") != args.count or len(records) != args.count
            or any(item.get("consistency") != "strong" or not isinstance(item.get("exact_readback"), dict)
                   for item in records)):
        raise RuntimeError("Simulator evidence does not contain the expected exact-readback results.")
    evidence["execution"] = {"instance_id": instance, "command_id": command_id,
                             "region": region, "simulator_sha256": source_hash}
    protected_json(args.output_file, evidence)
    print(f"Uploaded and exactly verified {args.count} synthetic readings from EC2.")
    print("Reading IDs: " + ", ".join(item["exact_readback"]["reading_id"] for item in records))
    print(f"Evidence saved: {args.output_file}")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--deployment-file", type=Path, default=PROJECT / "deployment.json")
    result.add_argument("--profile", help="Defaults to deployment.json's profile (normally my-aws)")
    result.add_argument("--count", type=int, default=12)
    result.add_argument("--output-file", type=Path, default=PROJECT / "evidence" / "ec2-upload.json")
    return result


if __name__ == "__main__":
    try:
        run(parser().parse_args())
    except (AwsError, OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"EC2 demo failed: {error}", file=sys.stderr)
        raise SystemExit(1)
