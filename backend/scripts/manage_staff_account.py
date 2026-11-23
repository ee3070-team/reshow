#!/usr/bin/env python3
"""IAM-only staff bootstrap. Never generates, prints or stores a password.

This local AWS-administration utility has no HTTP equivalent. Real invitations
are sent only with the explicit --delivery email option. Synthetic cleanup is
restricted to accounts marked synthetic and an example.com enrollment address.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app
import accounts
import auth
import workflow_store as store
import workflows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "retire-synthetic", "prepare"))
    parser.add_argument("--profile", default="my-aws")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack-name", default="ee3070-reshow-demo")
    parser.add_argument("--staff-id", required=True)
    parser.add_argument("--email")
    parser.add_argument("--name")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--delivery", choices=("suppress", "email"), default="suppress")
    parser.add_argument("--confirm-aws-controlled-bootstrap", action="store_true")
    args = parser.parse_args()
    app.identifier(args.staff_id, "staff_id")
    if args.action == "prepare":
        print(json.dumps({"action": "prepared_only", "staff_id": args.staff_id, "email": args.email,
                          "name": args.name, "role": "Administrator", "delivery": args.delivery,
                          "provider_created": False, "email_sent": False}))
        return
    if not args.confirm_aws_controlled_bootstrap:
        parser.error("AWS bootstrap/retirement requires --confirm-aws-controlled-bootstrap.")
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    caller = session.client("sts").get_caller_identity()
    if caller["Account"] != "835822826851":
        parser.error("Unexpected AWS account; no changes made.")
    outputs = session.client("cloudformation").describe_stacks(StackName=args.stack_name)["Stacks"][0]["Outputs"]
    values = {row["OutputKey"]: row["OutputValue"] for row in outputs}
    os.environ.update(COGNITO_USER_POOL_ID=values["StaffUserPoolId"], COGNITO_ISSUER=values["StaffIssuer"])
    app._table = session.resource("dynamodb").Table(values["TableName"])
    accounts._cognito = session.client("cognito-idp")
    if args.action == "retire-synthetic":
        previous = store.get(app, "account", args.staff_id)
        data = previous["data"]
        if not data.get("synthetic") or not data.get("invited_email", "").endswith("@example.com"):
            parser.error("Cleanup is limited to explicitly synthetic example.com accounts.")
        if data["status"] != "disabled":
            accounts.change(app, previous, {**data, "status": "disabled", "revoked_before": int(app.now_utc().timestamp())},
                            action="synthetic_bootstrap_retired", allow_zero=True)
        accounts.provider_call(app, "admin_disable_user", Username=data["username"])
        print(json.dumps({"staff_id": args.staff_id, "status": "disabled", "synthetic": True,
                          "records_preserved": True, "http_bootstrap_available": False}))
        return
    if not args.email or not args.name:
        parser.error("bootstrap requires --email and --name.")
    if args.synthetic and (not args.email.lower().endswith("@example.com") or args.delivery != "suppress"):
        parser.error("Synthetic bootstrap requires example.com and delivery suppress.")
    staff_body = {"staff_id": args.staff_id, "name": args.name, "email": args.email, "role": "Administrator"}
    # Reuses the same validated staff and unique-email registry as the API.
    workflows.save_staff(app, staff_body)
    previous = store.get(app, "account", args.staff_id, required=False)
    if previous:
        if previous["data"].get("invited_email") != args.email.lower() or not accounts.active_admin(previous["data"]):
            parser.error("Existing enrollment differs or is inactive. Inspect it; bootstrap will not overwrite it.")
        print(json.dumps({"status": "duplicate", "item": accounts.public(previous)}))
        return
    username = "reshow-" + hashlib.sha256(args.staff_id.encode()).hexdigest()[:40]
    try:
        user = accounts._cognito.admin_get_user(UserPoolId=os.environ["COGNITO_USER_POOL_ID"], Username=username)
    except accounts._cognito.exceptions.UserNotFoundException:
        kwargs = {"Username": username, "UserAttributes": [{"Name": "email", "Value": args.email.lower()},
                   {"Name": "email_verified", "Value": "true"}]}
        if args.delivery == "email":
            kwargs["DesiredDeliveryMediums"] = ["EMAIL"]
        else:
            kwargs["MessageAction"] = "SUPPRESS"
        user = accounts.provider_call(app, "admin_create_user", **kwargs)["User"]
    attrs = {row["Name"]: row["Value"] for row in user.get("Attributes", user.get("UserAttributes", []))}
    if attrs.get("email") != args.email.lower() or not attrs.get("sub"):
        parser.error("Reserved provider identity differs; no application account created.")
    data = {"staff_id": args.staff_id, "role": "Administrator", "status": "active", "username": username,
            "issuer": os.environ["COGNITO_ISSUER"], "subject": attrs["sub"], "invited_email": args.email.lower(),
            "delivery": args.delivery, "synthetic": args.synthetic, "provider_status": user.get("UserStatus", "FORCE_CHANGE_PASSWORD"),
            "revoked_before": -1}
    item = store.make(app, "account", args.staff_id, data)
    identity = store.make(app, "identity", auth.identity_key(data["issuer"], data["subject"]), {"staff_id": args.staff_id})
    control = store.get(app, "auth-control", "administrators", required=False)
    count = int(control["data"]["active_administrators"]) if control else 0
    next_control = store.make(app, "auth-control", "administrators", {"active_administrators": count + 1,
                               "http_bootstrap_closed": True}, control)
    accounts.transaction(app, [store.put_action(app, item), *store.index_actions(app, item), store.put_action(app, identity),
                              store.put_action(app, next_control, control), *accounts.audit(app, "aws_controlled_bootstrap", args.staff_id, None)])
    print(json.dumps({"status": "created", "item": accounts.public(item), "http_bootstrap_available": False,
                      "password_returned": False, "email_requested": args.delivery == "email"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider exceptions can include personal data; return only the type.
        print(json.dumps({"error": type(exc).__name__, "message": "Operation incomplete. Inspect current account and retry unchanged IDs."}), file=sys.stderr)
        sys.exit(1)
