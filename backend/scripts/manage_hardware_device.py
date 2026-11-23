#!/usr/bin/env python3
"""Enroll or revoke a ReShow hardware credential through AWS IAM."""

import argparse
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app
import auth
import workflow_store as store
import workflows

ACCOUNT = "835822826851"
REGION = "us-east-1"
STACK = "ee3070-reshow-demo"
TABLE = "ee3070-reshow-demo-readings"
API = "https://urauw4ckw1.execute-api.us-east-1.amazonaws.com"
ENROLLMENT_KIND = "hardware-enrollment"


class EnrollmentError(ValueError):
    pass


def target_table(session):
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise EnrollmentError("Unexpected AWS account.")
    stack, = session.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    if (stack["StackName"] != STACK
            or not stack["StackId"].startswith(f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/{STACK}/")
            or outputs.get("TableName") != TABLE or outputs.get("ApiBaseUrl") != API):
        raise EnrollmentError("Unexpected ReShow stack, table or API.")
    return session.resource("dynamodb").Table(TABLE)


@contextmanager
def private_directory(path):
    if not path.is_absolute():
        raise EnrollmentError("Use an absolute path to the private credential file.")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise EnrollmentError("The credential directory must be owned by you and have mode 0700.")
        yield directory
    finally:
        os.close(directory)


def credential_data(device_id):
    return {"api_base_url": API, "device_id": device_id, "source": "device",
            "post_paths": list(auth.HARDWARE_POST_PATHS)}


def read_credential(directory, name, device_id):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise EnrollmentError("The credential must be an owner-only regular file with mode 0600.")
        try:
            value = json.loads(handle.read(4097))
            token = value.pop("bearer_token")
            if value != credential_data(device_id) or not isinstance(token, str) or not auth.HARDWARE_TOKEN.fullmatch(token):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise EnrollmentError("The credential file does not match this device and API.") from None
    return token


def load_credential(directory, name, device_id, *, create):
    try:
        return read_credential(directory, name, device_id)
    except FileNotFoundError:
        if not create:
            raise EnrollmentError("Use the existing private file for this credential.") from None
    token = auth.HARDWARE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    temporary = ".reshow-" + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump({**credential_data(device_id), "bearer_token": token}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        except FileExistsError:
            return read_credential(directory, name, device_id)
        os.fsync(directory)
    finally:
        os.unlink(temporary, dir_fd=directory)
    return token


def bind_showcase(device_id, showcase_id, name):
    registration = store.get(app, "unique", "device#" + device_id, required=False)
    if registration is None:
        workflows.create_showcase(app, {"showcase_id": showcase_id, "name": name, "device_id": device_id})
    elif registration["data"].get("owner") != showcase_id or registration["data"].get("active") is not True:
        raise EnrollmentError("The device has a conflicting showcase registration.")
    showcase = workflows.bound_showcase(app, device_id)
    if showcase["SK"] != showcase_id or showcase["data"].get("device_id") != device_id:
        raise EnrollmentError("Showcase registration failed readback.")


def enroll(device_id, digest, enrollment):
    previous = store.get(app, auth.HARDWARE_CREDENTIAL_KIND, digest, required=False)
    data = {"device_id": device_id, "active": True}
    binding = {"digest": digest, "active": True}
    if previous:
        if previous["data"] != data or enrollment is None or enrollment["data"] != binding:
            raise EnrollmentError("This file refers to a revoked or conflicting credential. Use a new private file.")
        return
    if enrollment and enrollment["data"].get("active") is not False:
        raise EnrollmentError("The device already has an active credential. Use its original private file.")
    store.transact(app, [
        store.put_action(app, store.make(app, auth.HARDWARE_CREDENTIAL_KIND, digest, data)),
        store.put_action(app, store.make(app, ENROLLMENT_KIND, device_id, binding, enrollment), enrollment),
    ])
    if (store.get(app, auth.HARDWARE_CREDENTIAL_KIND, digest)["data"] != data
            or store.get(app, ENROLLMENT_KIND, device_id)["data"] != binding):
        raise EnrollmentError("Credential enrollment failed readback.")


def revoke(device_id, digest, enrollment):
    previous = store.get(app, auth.HARDWARE_CREDENTIAL_KIND, digest)
    if previous["data"].get("device_id") != device_id:
        raise EnrollmentError("The credential does not belong to this device.")
    if previous["data"].get("active") is False:
        return
    if not enrollment or enrollment["data"] != {"digest": digest, "active": True}:
        raise EnrollmentError("The credential does not match the active device enrollment.")
    store.transact(app, [
        store.put_action(app, store.make(app, auth.HARDWARE_CREDENTIAL_KIND, digest,
                                        {"device_id": device_id, "active": False}, previous), previous),
        store.put_action(app, store.make(app, ENROLLMENT_KIND, device_id, {"digest": digest, "active": False},
                                        enrollment), enrollment),
    ])
    if (store.get(app, auth.HARDWARE_CREDENTIAL_KIND, digest)["data"].get("active") is not False
            or store.get(app, ENROLLMENT_KIND, device_id)["data"].get("active") is not False):
        raise EnrollmentError("Credential revocation failed readback.")


def manage(args, session):
    device_id = app.identifier(args.device_id, "device_id")
    if auth.demo_device(device_id):
        raise EnrollmentError("The device ID is reserved for demo data.")
    issuing = args.action == "enroll"
    path = args.credential_file
    if issuing:
        showcase_id = app.identifier(args.showcase_id, "showcase_id")
        name = app.text(args.showcase_name, "showcase_name", 120)
    if not issuing and path is None:
        app._table = target_table(session)
        enrollment = store.get(app, ENROLLMENT_KIND, device_id)
        revoke(device_id, enrollment["data"]["digest"], enrollment)
        return {"device_id": device_id, "status": "revoked"}
    with private_directory(path) as directory:
        app._table = target_table(session)
        enrollment = store.get(app, ENROLLMENT_KIND, device_id, required=False)
        token = load_credential(directory, path.name, device_id,
                                create=issuing and (enrollment is None or enrollment["data"].get("active") is False))
        digest = auth.hardware_token_digest(token)
        if issuing:
            bind_showcase(device_id, showcase_id, name)
            enroll(device_id, digest, enrollment)
            if not hmac.compare_digest(token, read_credential(directory, path.name, device_id)):
                raise EnrollmentError("The credential file changed during enrollment.")
            return {**credential_data(device_id), "showcase_id": showcase_id, "credential_file": str(path),
                    "status": "active"}
        revoke(device_id, digest, enrollment)
        return {"device_id": device_id, "status": "revoked"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enroll", "revoke"))
    parser.add_argument("--profile", default="my-aws")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--showcase-id")
    parser.add_argument("--showcase-name")
    parser.add_argument("--credential-file", type=Path)
    args = parser.parse_args()
    if args.action == "enroll" and None in (args.showcase_id, args.showcase_name, args.credential_file):
        parser.error("enroll requires --showcase-id, --showcase-name and --credential-file.")
    try:
        import boto3
        result = manage(args, boto3.Session(profile_name=args.profile, region_name=REGION))
    except EnrollmentError as error:
        parser.exit(1, str(error) + "\n")
    except app.APIError as error:
        parser.exit(1, f"ReShow operation failed ({error.code}). Check the device and showcase before retrying.\n")
    except Exception as error:
        parser.exit(1, f"ReShow operation failed ({type(error).__name__}). Retry with the same private file.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
