#!/usr/bin/env python3
"""Deploy the EE3070 demo using AWS CLI credentials; never print the API token."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from urllib.parse import urlsplit


PROJECT = Path(__file__).resolve().parents[1]
TOKEN_PARAMETER = "/ee3070/reshow/demo/api-token"
ENDPOINT_PARAMETER = "/ee3070/reshow/demo/api-base-url"


class AwsError(RuntimeError):
    pass


class Aws:
    def __init__(self, profile, region, secret_values=()):
        self.profile = profile
        self.region = region
        self.secret_values = list(secret_values)

    def redact(self, message):
        for value in self.secret_values:
            message = message.replace(value, "[REDACTED]")
        return message

    def call(self, *args, timeout=None):
        command = ["aws", "--profile", self.profile, "--region", self.region,
                   "--no-cli-pager", "--output", "json", *args]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True,
                env={**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"},
                check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise AwsError("AWS CLI command timed out.") from None
        if result.returncode:
            raise AwsError(self.redact(result.stderr.strip() or result.stdout.strip()))
        return json.loads(result.stdout) if result.stdout.strip() else {}


def protected_json(path, value):
    """Atomically write secret-bearing state or CLI inputs with owner-only access."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            os.chmod(temporary, 0o600)
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def package_backend(backend_dir, archive, dependencies_dir=None):
    """Package sources and preinstalled dependencies for the target Lambda ABI.

    Build runtime-dependencies from requirements-runtime.txt for the deployed
    Linux/Python/architecture before calling this helper. Never import those
    target wheels into the local development interpreter.
    """
    required = ("app.py", "index.html", "openapi.json")
    for name in required:
        if not (backend_dir / name).is_file():
            raise ValueError(f"Required Lambda source is missing: {backend_dir / name}")
    # Explicit source roots exclude local configuration, tests and access state.
    sources = sorted(path for path in backend_dir.glob("*.py") if not path.name.startswith(".")) + [backend_dir / name for name in required if not name.endswith(".py")]
    asset_root = backend_dir / "assets"
    if asset_root.is_dir():
        sources += [path for path in sorted(asset_root.rglob("*")) if path.is_file()
                    and not any(part.startswith(".") or part == "__pycache__"
                                for part in path.relative_to(asset_root).parts)]
    dependencies_dir = Path(dependencies_dir) if dependencies_dir is not None else PROJECT / "runtime-dependencies"
    if not (dependencies_dir / "cryptography" / "__init__.py").is_file():
        raise ValueError("Pinned Lambda dependencies are missing; install requirements-runtime.txt into runtime-dependencies for the deployed runtime and architecture.")
    packaged = {source.relative_to(backend_dir).as_posix(): source for source in sources}
    for source in sorted(dependencies_dir.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"Do not package symlinked dependency files: {source}")
        if not source.is_file() or any(part.startswith(".") or part == "__pycache__" for part in source.relative_to(dependencies_dir).parts):
            continue
        name = source.relative_to(dependencies_dir).as_posix()
        if name in packaged:
            raise ValueError(f"Dependency file collides with backend source: {name}")
        packaged[name] = source
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, source in sorted(packaged.items()):
            if source.is_symlink():
                raise ValueError(f"Do not package symlinked backend files: {source}")
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, source.read_bytes())
    return hashlib.sha256(Path(archive).read_bytes()).hexdigest()


def ensure_artifact_bucket(aws, name, account):
    created = False
    try:
        aws.call("s3api", "head-bucket", "--bucket", name,
                 "--expected-bucket-owner", account)
    except AwsError as error:
        if "404" not in str(error) and "NoSuchBucket" not in str(error) and "Not Found" not in str(error):
            raise
        arguments = ["s3api", "create-bucket", "--bucket", name]
        if aws.region != "us-east-1":
            arguments += ["--create-bucket-configuration", f"LocationConstraint={aws.region}"]
        aws.call(*arguments)
        created = True
        aws.call("s3api", "put-bucket-tagging", "--bucket", name,
                 "--expected-bucket-owner", account,
                 "--tagging", json.dumps({"TagSet": [
                     {"Key": "Project", "Value": "EE3070"},
                     {"Key": "Component", "Value": "reshow-demo-artifacts"},
                 ]}))
    if not created:
        location = aws.call("s3api", "get-bucket-location", "--bucket", name,
                            "--expected-bucket-owner", account).get("LocationConstraint") or "us-east-1"
        if location != aws.region:
            raise ValueError(f"Artifact bucket is in {location}, expected {aws.region}.")
        tags = aws.call("s3api", "get-bucket-tagging", "--bucket", name,
                        "--expected-bucket-owner", account).get("TagSet", [])
        if {item["Key"]: item["Value"] for item in tags}.get("Component") != "reshow-demo-artifacts":
            raise ValueError("Existing artifact bucket is not tagged for this demo; choose another bucket.")
    aws.call("s3api", "put-public-access-block", "--bucket", name,
             "--expected-bucket-owner", account,
             "--public-access-block-configuration",
             "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true")
    aws.call("s3api", "put-bucket-encryption", "--bucket", name,
             "--expected-bucket-owner", account,
             "--server-side-encryption-configuration", json.dumps({"Rules": [{
                 "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
             }]}))
    aws.call("s3api", "put-bucket-ownership-controls", "--bucket", name,
             "--expected-bucket-owner", account,
             "--ownership-controls", json.dumps({"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}))


def ensure_media_cors(aws, bucket, account, origin):
    """Preserve unrelated rules; allow production and the configured local demo."""
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or "*" in origin):
        raise ValueError("--frontend-origin must be one exact HTTPS origin without a path or wildcard.")
    try:
        rules = aws.call("s3api", "get-bucket-cors", "--bucket", bucket,
                         "--expected-bucket-owner", account).get("CORSRules", [])
    except AwsError as error:
        if "NoSuchCORSConfiguration" not in str(error):
            raise
        rules = []
    rules = [rule for rule in rules if rule.get("ID") != "EE3070ReShowMedia"]
    if len(rules) >= 100:
        raise ValueError("Bucket already has 100 unrelated CORS rules; cannot add the media origin safely.")
    rules.append({"ID": "EE3070ReShowMedia", "AllowedOrigins": [origin, "http://localhost:8000"],
                  "AllowedMethods": ["POST", "GET", "HEAD"],
                  "AllowedHeaders": ["Content-Type", "x-amz-*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 300})
    aws.call("s3api", "put-bucket-cors", "--bucket", bucket, "--expected-bucket-owner", account,
             "--cors-configuration", json.dumps({"CORSRules": rules}))


def instance_network(aws, stack):
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    instance_id = outputs.get("InstanceId")
    if not instance_id:
        return None
    reservations = aws.call("ec2", "describe-instances", "--instance-ids", instance_id).get("Reservations", [])
    instances = [item for reservation in reservations for item in reservation.get("Instances", [])]
    if len(instances) != 1:
        raise ValueError("Cannot establish the existing EC2 instance/network state.")
    return {"instance_id": instance_id, "security_group_ids": sorted(group["GroupId"] for group in instances[0]["SecurityGroups"])}


def reject_ec2_changes(changes):
    if any(change.get("ResourceChange", {}).get("LogicalResourceId") == "SimulatorInstance" for change in changes):
        raise ValueError("Refusing this change set: it changes SimulatorInstance. Preserve its existing AMI, identity and attached frontend security group before deployment.")


def change_set_preview(aws, change_set_id):
    preview = aws.call("cloudformation", "describe-change-set", "--change-set-name", change_set_id)
    following = preview.get("NextToken")
    seen = set()
    while following:
        if following in seen:
            raise ValueError("CloudFormation change-set pagination did not advance.")
        seen.add(following)
        page = aws.call("cloudformation", "describe-change-set", "--change-set-name", change_set_id, "--next-token", following)
        preview.setdefault("Changes", []).extend(page.get("Changes", []))
        following = page.get("NextToken")
    preview.pop("NextToken", None)
    return preview


def put_parameter(aws, temporary_dir, name, value, parameter_type):
    request_file = Path(temporary_dir) / ("token-parameter.json" if parameter_type == "SecureString" else "endpoint-parameter.json")
    protected_json(request_file, {
        "Name": name,
        "Description": "EE3070 ReShow demo API configuration",
        "Value": value,
        "Type": parameter_type,
        "Overwrite": True,
        "Tier": "Standard",
    })
    aws.call("ssm", "put-parameter", "--cli-input-json", f"file://{request_file}")


def deploy(args):
    if not shutil.which("aws"):
        raise ValueError("AWS CLI v2 is required and was not found on PATH.")
    if not args.state_file.is_absolute():
        raise ValueError("--state-file must be an absolute path outside the deliverable directory.")
    state_file = args.state_file.resolve()
    if state_file == PROJECT or PROJECT in state_file.parents:
        raise ValueError("Keep --state-file outside the deliverable directory; it contains the bearer token.")
    if args.state_file.is_symlink():
        raise ValueError("The private state file must not be a symlink.")
    if state_file.exists():
        state = json.loads(state_file.read_text())
        for key, expected in (("stack_name", args.stack_name), ("region", args.region), ("account_id", args.expected_account)):
            if state.get(key) != expected:
                raise ValueError(f"Private state {key} does not match this deployment.")
        token = state.get("bearer_token", "")
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("Private state contains no valid bearer token.")
        os.chmod(state_file, 0o600)
    else:
        state = {
            "stack_name": args.stack_name, "region": args.region,
            "account_id": args.expected_account,
            "bearer_token": secrets.token_urlsafe(32),
        }
        token = state["bearer_token"]
    aws = Aws(args.profile, args.region, [token])
    identity = aws.call("sts", "get-caller-identity")
    if identity.get("Account") != args.expected_account:
        raise ValueError(f"Wrong AWS account: expected {args.expected_account}, received {identity.get('Account')}.")
    state["profile"] = args.profile
    protected_json(state_file, state)
    existing_stack = None
    try:
        existing_stack = aws.call("cloudformation", "describe-stacks", "--stack-name", args.stack_name)["Stacks"][0]
    except AwsError as error:
        if "does not exist" not in str(error):
            raise
    network_before = instance_network(aws, existing_stack) if existing_stack else None
    bucket = args.artifact_bucket or f"ee3070-reshow-artifacts-{args.expected_account}-{args.region}"
    with tempfile.TemporaryDirectory(prefix="ee3070-deploy-") as working:
        working = Path(working)
        archive = working / "backend.zip"
        digest = package_backend(args.backend_dir, archive)
        key = f"lambda/{digest}.zip"
        aws.call("cloudformation", "validate-template", "--template-body", f"file://{args.template}")
        print(f"Preparing private Lambda artifacts in {bucket}.", flush=True)
        ensure_artifact_bucket(aws, bucket, args.expected_account)
        ensure_media_cors(aws, bucket, args.expected_account, args.frontend_origin)
        aws.call("s3api", "put-object", "--bucket", bucket, "--key", key,
                 "--body", str(archive), "--server-side-encryption", "AES256",
                 "--expected-bucket-owner", args.expected_account)
        parameters = [
            {"ParameterKey": "VpcId", "ParameterValue": args.vpc_id},
            {"ParameterKey": "SubnetId", "ParameterValue": args.subnet_id},
            {"ParameterKey": "ArtifactBucket", "ParameterValue": bucket},
            {"ParameterKey": "BackendKey", "ParameterValue": key},
            {"ParameterKey": "ApiToken", "ParameterValue": token},
            {"ParameterKey": "TokenParameterName", "ParameterValue": TOKEN_PARAMETER},
            {"ParameterKey": "EndpointParameterName", "ParameterValue": ENDPOINT_PARAMETER},
        ]
        if existing_stack:
            # Do not deliberately advance the simulator's AMI. CloudFormation
            # can still re-resolve SSM parameter values; the change-set guard
            # below refuses any resulting instance change before execution.
            parameters.append({"ParameterKey": "LatestAmiId", "UsePreviousValue": True})
        parameter_file = working / "cloudformation-parameters.json"
        protected_json(parameter_file, parameters)
        exists = existing_stack is not None
        print(f"Preparing CloudFormation change set for {args.stack_name}.", flush=True)
        common = ["--stack-name", args.stack_name, "--template-body", f"file://{args.template}",
                  "--parameters", f"file://{parameter_file}", "--capabilities", "CAPABILITY_IAM",
                  "--tags", "Key=Project,Value=EE3070", "Key=Component,Value=reshow-demo"]
        change_set_name = "reshow-" + uuid.uuid4().hex
        change_set = aws.call("cloudformation", "create-change-set", *common,
                              "--change-set-name", change_set_name,
                              "--change-set-type", "UPDATE" if exists else "CREATE")
        try:
            aws.call("cloudformation", "wait", "change-set-create-complete", "--change-set-name", change_set["Id"])
        except AwsError as error:
            preview = change_set_preview(aws, change_set["Id"])
            reason = preview.get("StatusReason", "")
            if not exists or not any(text in reason for text in ("didn't contain changes", "No updates are to be performed")):
                raise
            print("CloudFormation reports no infrastructure changes.", flush=True)
            if args.prepare_only:
                protected_json(args.output_file, {"change_set_id": change_set["Id"], "changes": [],
                    "change_set_status": preview.get("Status"), "status_reason": reason,
                    "backend_sha256": digest, "artifact_key": key, "instance_network_before": network_before})
                print(f"No executable changes. Review details: {args.output_file}")
                return
        else:
            preview = change_set_preview(aws, change_set["Id"])
            changes = preview.get("Changes", [])
            if exists:
                reject_ec2_changes(changes)
            prepared = {"profile": args.profile, "region": args.region, "account_id": args.expected_account,
                        "stack_name": args.stack_name, "backend_sha256": digest, "artifact_key": key,
                        "change_set_id": change_set["Id"], "change_set_status": preview.get("Status"),
                        "changes": changes, "instance_network_before": network_before, "frontend_origin": args.frontend_origin}
            if args.prepare_only:
                protected_json(args.output_file, prepared)
                print(f"Change set prepared without execution: {change_set['Id']}")
                print(f"Reviewable change details: {args.output_file}")
                return
            aws.call("cloudformation", "execute-change-set", "--change-set-name", change_set["Id"])
            waiter = "stack-update-complete" if exists else "stack-create-complete"
            try:
                aws.call("cloudformation", "wait", waiter, "--stack-name", args.stack_name)
            except AwsError:
                events = aws.call("cloudformation", "describe-stack-events", "--stack-name", args.stack_name)
                failures = [f"{event['LogicalResourceId']}: {event.get('ResourceStatusReason', 'failed')}"
                            for event in events.get("StackEvents", [])
                            if event.get("ResourceStatus", "").endswith("_FAILED")][:8]
                if failures:
                    print(aws.redact("\n".join(failures)), file=sys.stderr)
                raise
        stack = aws.call("cloudformation", "describe-stacks", "--stack-name", args.stack_name)["Stacks"][0]
        if network_before is not None and instance_network(aws, stack) != network_before:
            raise ValueError("EC2 instance identity or attached security groups changed unexpectedly; inspect the deployment before continuing.")
        outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
        if not outputs.get("ApiBaseUrl"):
            raise ValueError("CloudFormation did not return ApiBaseUrl.")
        put_parameter(aws, working, TOKEN_PARAMETER, token, "SecureString")
        put_parameter(aws, working, ENDPOINT_PARAMETER, outputs["ApiBaseUrl"], "String")
        state.update({"api_base_url": outputs["ApiBaseUrl"], "token_parameter_name": TOKEN_PARAMETER,
                      "endpoint_parameter_name": ENDPOINT_PARAMETER})
        protected_json(state_file, state)
        public = {"profile": args.profile, "region": args.region, "account_id": args.expected_account,
                  "stack_name": args.stack_name, "stack_id": stack["StackId"],
                  "backend_sha256": digest, "artifact_key": key,
                  "instance_network_preserved": network_before is not None, **outputs}
        protected_json(args.output_file, public)
    print(f"Deployment complete. HTTPS API: {outputs['ApiBaseUrl']}")
    print(f"New simulator instance: {outputs['InstanceId']}")
    print(f"Nonsecret deployment details: {args.output_file}")
    print(f"Private access state (mode 0600): {state_file}")
    print("Run scripts/run_ec2_demo.py to upload and verify dummy data from the new instance.")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile", default="my-aws")
    result.add_argument("--region", default="us-east-1")
    result.add_argument("--expected-account", default="835822826851")
    result.add_argument("--stack-name", default="ee3070-reshow-demo")
    result.add_argument("--vpc-id", default="vpc-0ed58f86ff88a7c2b")
    result.add_argument("--subnet-id", default="subnet-03ba974bfd19b9e1a")
    result.add_argument("--artifact-bucket")
    result.add_argument("--frontend-origin", default="https://dkbznu9f5nh9n.cloudfront.net",
                        help="Exact HTTPS browser origin allowed to use the private media S3 forms.")
    result.add_argument("--prepare-only", action="store_true",
                        help="Upload artifacts/configure media CORS, prepare and output a guarded change set without executing it.")
    result.add_argument("--state-file", required=True, type=Path,
                        help="Absolute private JSON path outside this project (contains bearer token).")
    result.add_argument("--output-file", type=Path, default=PROJECT / "deployment.json")
    result.add_argument("--template", type=Path, default=PROJECT / "infra" / "template.json")
    result.add_argument("--backend-dir", type=Path, default=PROJECT / "backend")
    return result


if __name__ == "__main__":
    os.umask(0o077)
    arguments = parser().parse_args()
    try:
        deploy(arguments)
    except (AwsError, OSError, ValueError, KeyError) as error:
        # AwsError output has already been redacted; never dump local JSON state.
        print(f"Deployment failed: {error}", file=sys.stderr)
        sys.exit(1)
