"""Administrator-controlled Cognito enrollment and authoritative website roles.

Provider operations are deliberately resumable: deterministic usernames and an
immutable invitation request precede Cognito creation. Setup links return a new
temporary credential once, without storing it. Permanent passwords go directly
to Cognito. Directory/card records remain separate.
"""
import hashlib
import os
import re
import secrets
from urllib.parse import urlencode

import auth
import workflow_store as store

_cognito = None


def provider(api):
    global _cognito
    if not os.environ.get("COGNITO_USER_POOL_ID"):
        raise api.APIError(503, "service_unavailable", "Staff sign-in is not configured.")
    if _cognito is None:
        import boto3
        _cognito = boto3.client("cognito-idp")
    return _cognito


def provider_call(api, operation, **kwargs):
    try:
        return getattr(provider(api), operation)(UserPoolId=os.environ["COGNITO_USER_POOL_ID"], **kwargs)
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code in ("TooManyRequestsException", "LimitExceededException"):
            raise api.APIError(429, "rate_limited", "The identity provider is busy. Retry the unchanged request with backoff.") from None
        if code in ("InvalidParameterException", "InvalidPasswordException", "UserNotFoundException"):
            raise api.APIError(409, "identity_operation_failed", "The identity operation could not be completed. Check the current account and retry safely.") from None
        raise


def public(item):
    data = item["data"]
    keys = ("staff_id", "role", "status", "issuer", "subject", "invitation_id", "invited_email", "delivery",
            "provider_status", "synthetic", "last_invitation_at")
    return {**{key: data[key] for key in keys if key in data}, "version": item["version"],
            "created_at": item["created_at"], "updated_at": item["updated_at"]}


def guard(api):
    item = store.get(api, "auth-control", "administrators", required=False)
    if item is None:
        raise api.APIError(503, "bootstrap_required", "A controlled AWS administrator bootstrap is required.")
    return item


def active_admin(data):
    return data.get("role") == "Administrator" and data.get("status") == "active"


def transaction(api, actions, caller=None, target_id=None):
    # Account-administration privileges cannot survive a concurrent demotion.
    # A self-target Put already checks that same current account version.
    if caller and caller["staff_id"] != target_id:
        item = caller["account"]
        actions.append({"ConditionCheck": {"TableName": api.get_table().name,
            "Key": {"PK": item["PK"], "SK": item["SK"]}, "ConditionExpression": "#version = :version",
            "ExpressionAttributeNames": {"#version": "version"},
            "ExpressionAttributeValues": {":version": item["version"]}}})
    try:
        api.get_table().meta.client.transact_write_items(TransactItems=actions)
    except Exception as exc:
        details = getattr(exc, "response", {})
        if details.get("Error", {}).get("Code") == "TransactionCanceledException" and any(
                row.get("Code") == "ConditionalCheckFailed" for row in details.get("CancellationReasons", [])):
            raise api.APIError(409, "version_conflict", "Account state changed. Read current state and retry.") from None
        raise


def audit(api, action, staff_id, caller, detail=None):
    import uuid
    actor = {"staff_id": caller["staff_id"], "subject": caller["subject"], "issuer": caller["issuer"], "role": caller["role"]} if caller else {"kind": "aws-controlled-bootstrap"}
    item = store.make(api, "account-audit", uuid.uuid4().hex,
                      {"action": action, "staff_id": staff_id, "actor": actor, "detail": detail or {}})
    return [store.put_action(api, item), *store.index_actions(api, item)]


def change(api, previous, data, caller=None, action="account_updated", extra=None, allow_zero=False):
    control = guard(api)
    count = int(control["data"]["active_administrators"]) + int(active_admin(data)) - int(active_admin(previous["data"]))
    if count < 1 and not allow_zero:
        raise api.APIError(409, "last_administrator", "The last active Administrator cannot be disabled or demoted.")
    item = store.make(api, "account", previous["SK"], data, previous)
    next_control = store.make(api, "auth-control", "administrators", {**control["data"], "active_administrators": count}, control)
    transaction(api, [store.put_action(api, item, previous), store.put_action(api, next_control, control),
                     *audit(api, action, previous["SK"], caller), *(extra or [])], caller, previous["SK"])
    return item


def activate(api, previous):
    data = previous["data"]
    caller = {"staff_id": previous["SK"], "subject": data["subject"], "issuer": data["issuer"],
              "role": data["role"], "account": previous}
    return change(api, previous, {**data, "status": "active", "provider_status": "CONFIRMED"}, caller, "first_sign_in")


def role(api, value):
    if value not in auth.ROLES:
        raise api.APIError(400, "invalid_payload", "role must be Administrator, Operator or Viewer.")
    return value


def expected_version(api, headers):
    value = headers.get("if-match", "")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
        raise api.APIError(400, "invalid_payload", "If-Match must contain the current positive integer version.")
    return int(value)


def invite(api, body, person):
    required = {"invitation_id", "staff_id", "role"}
    if not required <= set(body) or set(body) - required - {"delivery"}:
        raise api.APIError(400, "invalid_payload", "Provide invitation_id, staff_id, role and optional delivery.")
    identity = {"invitation_id": api.identifier(body["invitation_id"], "invitation_id"),
                "staff_id": api.identifier(body["staff_id"], "staff_id"), "role": role(api, body["role"]),
                "delivery": body.get("delivery", "suppress")}
    if identity["delivery"] not in ("suppress", "email"):
        raise api.APIError(400, "invalid_payload", "delivery must be suppress or email.")
    staff = store.get(api, "staff", identity["staff_id"])
    email = staff["data"]["email"]
    control = guard(api)
    pending = store.get(api, "invitation", identity["invitation_id"], required=False)
    previous = store.get(api, "account", identity["staff_id"], required=False)
    if pending and pending["data"]["request"] != identity:
        raise api.APIError(409, "id_conflict", "The invitation ID belongs to another request.")
    if previous and previous["data"].get("invitation_id") != identity["invitation_id"]:
        raise api.APIError(409, "account_exists", "This staff record already has an account enrollment.")
    if previous and previous["data"].get("subject"):
        return api.response(200, {"status": "duplicate", "item": public(previous)})
    username = "reshow-" + hashlib.sha256(identity["staff_id"].encode()).hexdigest()[:40]
    if pending is None:
        pending = store.make(api, "invitation", identity["invitation_id"], {"request": identity, "username": username, "email": email})
        account = store.make(api, "account", identity["staff_id"], {**identity, "status": "provisioning", "username": username,
            "invited_email": email, "issuer": os.environ["COGNITO_ISSUER"], "revoked_before": -1})
        # Serialize account administration against last-admin updates, without
        # changing the count for an invitation that has not signed in yet.
        next_control = store.make(api, "auth-control", "administrators", control["data"], control)
        transaction(api, [store.put_action(api, pending), store.put_action(api, account),
                         *store.index_actions(api, account), store.put_action(api, next_control, control),
                         *audit(api, "invitation_prepared", identity["staff_id"], person)], person, identity["staff_id"])
        previous = account
    # Retrieve before creating: a provider success followed by a DB failure is
    # resumed without another identity or repeated invitation email.
    try:
        user = getattr(provider(api), "admin_get_user")(UserPoolId=os.environ["COGNITO_USER_POOL_ID"], Username=username)
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") != "UserNotFoundException":
            raise
        options = {"Username": username, "UserAttributes": [{"Name": "email", "Value": pending["data"]["email"]},
                    {"Name": "email_verified", "Value": "true"}]}
        if identity["delivery"] == "email":
            options["DesiredDeliveryMediums"] = ["EMAIL"]
        else:
            options["MessageAction"] = "SUPPRESS"
        try:
            user = provider_call(api, "admin_create_user", **options)["User"]
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") != "UsernameExistsException":
                raise
            user = provider_call(api, "admin_get_user", Username=username)
    attributes = user.get("Attributes", user.get("UserAttributes", []))
    values = {entry["Name"]: entry["Value"] for entry in attributes}
    subject = values.get("sub")
    if not subject or values.get("email") != pending["data"]["email"]:
        raise api.APIError(409, "identity_conflict", "The reserved provider identity does not match this enrollment.")
    link = store.make(api, "identity", auth.identity_key(os.environ["COGNITO_ISSUER"], subject), {"staff_id": identity["staff_id"]})
    data = {**previous["data"], "subject": subject, "status": "invited",
            "provider_status": user.get("UserStatus", "FORCE_CHANGE_PASSWORD"), "last_invitation_at": api.timestamp(api.now_utc())}
    item = change(api, previous, data, person, "invitation_linked", [store.put_action(api, link)])
    return api.response(201, {"status": "created", "item": public(item)})


def patch_account(api, staff_id, body, headers, person):
    if not body or set(body) - {"role", "status"}:
        raise api.APIError(400, "invalid_payload", "PATCH supports role and/or status only.")
    previous = store.get(api, "account", staff_id)
    if int(previous["version"]) != expected_version(api, headers):
        raise api.APIError(409, "version_conflict", "If-Match does not match the current account version.")
    data = dict(previous["data"])
    if "role" in body:
        data["role"] = role(api, body["role"])
    if "status" in body:
        if body["status"] not in ("active", "disabled"):
            raise api.APIError(400, "invalid_payload", "status must be active or disabled.")
        if not data.get("subject"):
            raise api.APIError(409, "account_not_ready", "Complete enrollment before changing website access.")
        # Restoring an invited user must not falsely claim first sign-in.
        data["status"] = "invited" if body["status"] == "active" and data.get("provider_status") == "FORCE_CHANGE_PASSWORD" else body["status"]
        if body["status"] == "disabled":
            data["revoked_before"] = int(api.now_utc().timestamp())
    item = change(api, previous, data, person)
    # Application state is authoritative and was changed first. Provider calls
    # are an additional defense, resumable by repeating the current PATCH.
    provider_call(api, "admin_disable_user" if data["status"] == "disabled" else "admin_enable_user", Username=data["username"])
    return api.response(200, {"item": public(item)})


def resend(api, staff_id, body, person):
    api.exact_fields(body, ("request_id",))
    request_id = api.identifier(body["request_id"], "request_id")
    previous = store.get(api, "account", staff_id)
    if previous["data"].get("status") != "invited":
        raise api.APIError(409, "account_not_invited", "Only an invited account can receive another invitation.")
    marker = store.get(api, "invitation-resend", request_id, required=False)
    if marker:
        if marker["data"]["staff_id"] != staff_id:
            raise api.APIError(409, "id_conflict", "The request ID belongs to another account.")
        return api.response(200, {"status": "duplicate", "item": public(previous)})
    # Reserve before provider call; ambiguous provider timeout never blindly
    # sends another email. Admin checks current account and chooses a fresh ID.
    marker = store.make(api, "invitation-resend", request_id, {"staff_id": staff_id, "outcome": "requested"})
    transaction(api, [store.put_action(api, marker), *audit(api, "invitation_resend_requested", staff_id, person)], person, None)
    provider_call(api, "admin_create_user", Username=previous["data"]["username"], MessageAction="RESEND", DesiredDeliveryMediums=["EMAIL"])
    return api.response(200, {"status": "requested", "item": public(previous),
                              "note": "Provider resend requested; an HTTP timeout does not prove email delivery failed."})


def setup_link(api, staff_id, body, headers, person):
    """Return a new temporary credential once, never persist it or include it in audit."""
    api.exact_fields(body, ())
    if not os.environ.get("COGNITO_SETUP_CLIENT_ID"):
        raise api.APIError(503, "service_unavailable", "Password setup has not been deployed yet.")
    previous = store.get(api, "account", staff_id)
    if previous["version"] != expected_version(api, headers):
        raise api.APIError(409, "version_conflict", "Reload the account before generating a setup link.")
    data = previous["data"]
    if data.get("status") != "invited" or not data.get("subject"):
        raise api.APIError(409, "account_not_invited", "Setup links are only available for pending invitations.")
    user = provider_call(api, "admin_get_user", Username=data["username"])
    if user.get("UserStatus") != "FORCE_CHANGE_PASSWORD" or user.get("Enabled") is False:
        raise api.APIError(409, "setup_unavailable", "This account has already completed password setup or is disabled. Use normal sign-in or password recovery.")
    # Serialize concurrent administrator requests and recheck caller permissions.
    item = change(api, previous, data, person, "setup_link_requested")
    temporary = "Aa1!" + secrets.token_urlsafe(32)
    provider_call(api, "admin_set_user_password", Username=data["username"], Password=temporary, Permanent=False)
    fragment = urlencode({"username": data["username"], "credential": temporary})
    url = os.environ["FRONTEND_ORIGIN"].rstrip("/") + "/auth/setup.html#" + fragment
    return api.response(200, {"setup_url": url, "item": public(item)},
                        {"cache-control": "no-store", "pragma": "no-cache"})


def session(api, person):
    staff = store.get(api, "staff", person["staff_id"])
    role_name = person["role"]
    return {"staff_id": person["staff_id"], "subject": person["subject"], "issuer": person["issuer"],
            "name": staff["data"]["name"], "email": staff["data"]["email"], "role": role_name,
            "account_status": "active", "account_version": person["account"]["version"],
            "permissions": {"view": True, "save_reports": role_name != "Viewer", "edit_exhibits": role_name != "Viewer",
                            "device_commands": role_name != "Viewer", "manage_staff": role_name == "Administrator",
                            "manage_accounts": role_name == "Administrator", "physical_cards": role_name == "Administrator",
                            "submit_producer_evidence": False}}


def route(api, method, path, query, event, headers):
    person = auth.principal.get()
    if person is None or person["kind"] != "human":
        return None
    if path == "/api/session" and method == "GET":
        if query:
            raise api.APIError(400, "invalid_query", "Session does not accept query parameters.")
        return api.response(200, {"user": session(api, person)})
    if path == "/api/session/logout" and method == "POST":
        api.exact_fields(api.parse_body(event, headers), ())
        previous = person["account"]
        item = change(api, previous, {**previous["data"], "revoked_before": int(api.now_utc().timestamp())}, person, "sessions_revoked")
        try:
            provider_call(api, "admin_user_global_sign_out", Username=item["data"]["username"])
            provider_revoked = True
        except Exception:
            provider_revoked = False
        return api.response(200, {"status": "signed_out", "application_sessions_revoked": True,
                                  "provider_revoked": provider_revoked, "end_provider_browser_session": True})
    if path == "/api/accounts" and method == "GET":
        if set(query) - {"limit", "cursor"}:
            raise api.APIError(400, "invalid_query", "Only limit and cursor are supported.")
        result = store.list_records(api, "account", query)
        result["items"] = [public(store.get(api, "account", row["staff_id"])) for row in result["items"]]
        return api.response(200, result)
    if path == "/api/accounts/invitations" and method == "POST":
        return invite(api, api.parse_body(event, headers), person)
    match = re.fullmatch(r"/api/accounts/([A-Za-z0-9_-]+)(/resend-invitation|/setup-link)?", path)
    if match:
        staff_id = api.identifier(match[1], "staff_id")
        if match[2] == "/setup-link" and method == "POST":
            return setup_link(api, staff_id, api.parse_body(event, headers), headers, person)
        if match[2] == "/resend-invitation" and method == "POST":
            return resend(api, staff_id, api.parse_body(event, headers), person)
        if not match[2] and method == "GET":
            return api.response(200, {"item": public(store.get(api, "account", staff_id))})
        if not match[2] and method == "PATCH":
            return patch_account(api, staff_id, api.parse_body(event, headers), headers, person)
    return None
