#!/usr/bin/env python3
"""Prepare, but never execute, a gateway-only CloudFormation change set."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import uuid

from deploy import Aws, AwsError, PROJECT, protected_json


ADDITIONS = {
    "ApiAccessLogGroup": "AWS::Logs::LogGroup",
    "StaffApiRootOptionsRoute": "AWS::ApiGatewayV2::Route",
}


def verify_scope(before, after):
    """Refuse stale templates or any modification outside the reviewed gateway patch."""
    old, new = copy.deepcopy(before), copy.deepcopy(after)
    previous, desired = old.pop("Resources"), new.pop("Resources")
    if old != new or set(previous) - set(desired):
        raise ValueError("The template changes stack metadata or removes resources.")
    added = set(desired) - set(previous)
    if added - set(ADDITIONS):
        raise ValueError("The template adds unrelated resources.")
    for name in added:
        if desired[name].get("Type") != ADDITIONS[name]:
            raise ValueError("The template adds an unexpected resource type.")
    for name in set(previous) & set(desired):
        left, right = copy.deepcopy(previous[name]), copy.deepcopy(desired[name])
        if name == "ApiStage":
            for item in (left, right):
                item.pop("DependsOn", None)
                for key in ("RouteSettings", "AccessLogSettings"):
                    item["Properties"].pop(key, None)
        if left != right:
            raise ValueError(f"The template changes an unrelated resource/property: {name}.")


def preview_changes(aws, change_set_id):
    changes, next_token, status, reason = [], None, None, None
    while True:
        args = ["cloudformation", "describe-change-set", "--change-set-name", change_set_id,
                "--no-paginate"]
        if next_token:
            args += ["--next-token", next_token]
        page = aws.call(*args)
        changes.extend(page.get("Changes", []))
        status, reason = page.get("Status"), page.get("StatusReason")
        next_token = page.get("NextToken")
        if not next_token:
            break
    for entry in changes:
        change = entry.get("ResourceChange", {})
        name, action = change.get("LogicalResourceId"), change.get("Action")
        allowed = ((name == "ApiStage" and action == "Modify" and change.get("Replacement") == "False")
                   or (name in ADDITIONS and action == "Add"
                       and change.get("ResourceType") == ADDITIONS[name]))
        if not allowed:
            raise ValueError(f"Refusing change set: unexpected {action} of {name}.")
    return {"change_set_id": change_set_id, "status": status, "status_reason": reason,
            "changes": changes, "executed": False}


def prepare(args, aws=None):
    aws = aws or Aws(args.profile, args.region)
    if aws.call("sts", "get-caller-identity").get("Account") != args.expected_account:
        raise ValueError("Wrong AWS account; no change set was created.")
    stack = aws.call("cloudformation", "describe-stacks", "--stack-name", args.stack_name)["Stacks"][0]
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"):
        raise ValueError("Stack is not in a stable state; no change set was created.")
    current = aws.call("cloudformation", "get-template", "--stack-name", args.stack_name)["TemplateBody"]
    if isinstance(current, str):
        current = json.loads(current)
    desired = json.loads(args.template.read_text())
    verify_scope(current, desired)
    with tempfile.TemporaryDirectory(prefix="reshow-gateway-") as working:
        parameters = Path(working) / "parameters.json"
        # This includes NoEcho API-token and current BackendKey parameters without reading either value.
        protected_json(parameters, [{"ParameterKey": key, "UsePreviousValue": True}
                                    for key in current.get("Parameters", {})])
        aws.call("cloudformation", "validate-template", "--template-body", f"file://{args.template}")
        created = aws.call("cloudformation", "create-change-set", "--stack-name", args.stack_name,
                           "--change-set-name", "reshow-gateway-" + uuid.uuid4().hex,
                           "--change-set-type", "UPDATE", "--template-body", f"file://{args.template}",
                           "--parameters", f"file://{parameters}", "--capabilities", "CAPABILITY_IAM")
        try:
            aws.call("cloudformation", "wait", "change-set-create-complete", "--change-set-name", created["Id"])
        except AwsError:
            result = preview_changes(aws, created["Id"])
            protected_json(args.output_file, result)
            raise ValueError("Change set did not become executable; inspect its recorded status.") from None
        result = preview_changes(aws, created["Id"])
        protected_json(args.output_file, result)
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile", default="my-aws")
    result.add_argument("--region", default="us-east-1")
    result.add_argument("--expected-account", default="835822826851")
    result.add_argument("--stack-name", default="ee3070-reshow-demo")
    result.add_argument("--template", type=Path, default=PROJECT / "infra/template.json")
    result.add_argument("--output-file", type=Path, required=True)
    return result


if __name__ == "__main__":
    try:
        outcome = prepare(parser().parse_args())
        print(json.dumps({"change_set_id": outcome["change_set_id"], "status": outcome["status"],
                          "executed": False}))
    except (AwsError, ValueError) as error:
        raise SystemExit(str(error)) from None
