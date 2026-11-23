#!/usr/bin/env python3
"""Prepare the independent staff-pagination key through IAM, never HTTP.

The default is read-only. --provision creates one random key conditionally;
unchanged retries preserve the existing key and all outstanding cursors.
Neither key material nor provider credentials are printed.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import secrets


KEY = {"PK": "WF#auth-control", "SK": "staff-cursor-key-v1"}


def valid(item):
    try:
        return (item["data"]["purpose"] == "staff-workflow-cursors-v1" and
                len(base64.b64decode(item["data"]["encryption_key"], altchars=b"-_", validate=True)) == 32)
    except (KeyError, TypeError, ValueError):
        return False


def prepare(session, provision=False):
    if session.client("sts").get_caller_identity()["Account"] != "835822826851":
        raise ValueError("Unexpected AWS account; no changes made.")
    stack = session.client("cloudformation").describe_stacks(StackName="ee3070-reshow-demo")["Stacks"][0]
    outputs = {row["OutputKey"]: row["OutputValue"] for row in stack["Outputs"]}
    table = session.resource("dynamodb").Table(outputs["TableName"])
    item = table.get_item(Key=KEY, ConsistentRead=True).get("Item")
    if item:
        if not valid(item):
            raise ValueError("The existing cursor key record requires operator inspection; it was not overwritten.")
        return {"status": "ready", "created": False, "key_material_returned": False}
    if not provision:
        return {"status": "prepared_only", "created": False, "key_material_returned": False}
    at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    data = {"purpose": "staff-workflow-cursors-v1", "encryption_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
    item = {**KEY, "data": data, "version": 1, "created_at": at, "updated_at": at}
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        item = table.get_item(Key=KEY, ConsistentRead=True).get("Item")
        if not valid(item):
            raise ValueError("A conflicting cursor key record was preserved; inspect it before retrying.") from None
        return {"status": "ready", "created": False, "key_material_returned": False}
    return {"status": "ready", "created": True, "key_material_returned": False}


def main():
    import boto3
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="my-aws")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--provision", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(boto3.Session(profile_name=args.profile, region_name=args.region), args.provision)))
    except Exception as exc:
        print(json.dumps({"status": "incomplete", "error": type(exc).__name__, "key_material_returned": False}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
