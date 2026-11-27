"""Gateway-verified Cognito access tokens and current application permissions.

JWT cryptography is performed by the HTTP API JWT authorizer on the dedicated
staff routes. The default route never accepts a human token. Application roles
and disable/logout state are read strongly consistently for every request.
"""
import hashlib
import json
import os
import re
from contextvars import ContextVar

import workflow_store as store

principal = ContextVar("reshow_principal", default=None)
ROLES = ("Administrator", "Operator", "Viewer")
SCOPE = "reshow/staff"
PRODUCER_COLLECTIONS = ("readings", "events", "proximity-events", "coverage", "display-history", "warnings",
                        "device-status", "tag-scans", "access-attempts")
HARDWARE_TOKEN_PREFIX = "rshw_hw_v1."
HARDWARE_TOKEN = re.compile(re.escape(HARDWARE_TOKEN_PREFIX) + r"[A-Za-z0-9_-]{43}")
HARDWARE_CREDENTIAL_KIND = "hardware-credential"
HARDWARE_POST_PATHS = ("/api/device-status", "/api/readings", "/api/rfid-events", "/api/tag-scans", "/api/access-attempts")
HARDWARE_COMMAND_TYPES = ("assignment", "label", "lighting")


def enforced():
    # Legacy behavior is available only through an explicit local-test setting;
    # a missing deployment flag must never restore the shared-token bypass.
    return os.environ.get("AUTH_ENFORCED", "true").lower() != "false"


def config(api):
    names = ("COGNITO_ISSUER", "COGNITO_CLIENT_ID", "COGNITO_DOMAIN", "FRONTEND_ORIGIN")
    if any(not os.environ.get(name) for name in names):
        raise api.APIError(503, "service_unavailable", "Staff sign-in is not configured.")
    origin = os.environ["FRONTEND_ORIGIN"].rstrip("/")
    return {"issuer": os.environ["COGNITO_ISSUER"], "client_id": os.environ["COGNITO_CLIENT_ID"],
            **({"setup_client_id": os.environ["COGNITO_SETUP_CLIENT_ID"]} if os.environ.get("COGNITO_SETUP_CLIENT_ID") else {}),
            "authorization_endpoint": os.environ["COGNITO_DOMAIN"] + "/oauth2/authorize",
            "token_endpoint": os.environ["COGNITO_DOMAIN"] + "/oauth2/token",
            "revocation_endpoint": os.environ["COGNITO_DOMAIN"] + "/oauth2/revoke",
            "logout_endpoint": os.environ["COGNITO_DOMAIN"] + "/logout",
            "redirect_uri": origin + "/auth/callback.html", "logout_uri": origin + "/",
            "allowed_callback_urls": [origin + "/auth/callback.html", "http://localhost:8000/auth/callback.html"],
            "allowed_logout_urls": [origin + "/", "http://localhost:8000/"],
            "scopes": [SCOPE], "staff_api_prefix": "/staff-api",
            "access_token_seconds": 900, "persistent_login": False,
            "refresh_policy": "Tokens stay in memory. Renew by a new code/PKCE login through the provider session; no refresh loop.",
            "logout_policy": "Application logout invalidates all existing access tokens for this account immediately; then end the provider browser session."}


def identity_key(issuer, subject):
    return hashlib.sha256(issuer.encode()).hexdigest() + "#" + subject


def human(api, event):
    context = event.get("requestContext", {})
    if (context.get("routeKey") not in ("ANY /staff-api/{proxy+}", "ANY /staff-api")
            or context.get("apiId") != os.environ.get("HTTP_API_ID")):
        raise api.APIError(401, "unauthorized", "A valid staff access token is required.")
    jwt = context.get("authorizer", {}).get("jwt", {})
    claims = jwt.get("claims", {})
    now = int(api.now_utc().timestamp())
    try:
        valid = (claims.get("iss") == os.environ["COGNITO_ISSUER"]
                 and claims.get("client_id") == os.environ["COGNITO_CLIENT_ID"]
                 and claims.get("token_use") == "access"
                 and int(claims["exp"]) > now and int(claims["iat"]) <= now + 60
                 and int(claims.get("nbf", 0)) <= now + 60
                 and isinstance(claims.get("sub"), str) and 1 <= len(claims["sub"]) <= 128
                 and SCOPE in set(jwt.get("scopes", [])))
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise api.APIError(401, "unauthorized", "A valid staff access token is required.")
    identity = store.get(api, "identity", identity_key(claims["iss"], claims["sub"]), required=False)
    if identity is None:
        raise api.APIError(403, "account_unavailable", "This identity has no active website account.")
    account = store.get(api, "account", identity["data"]["staff_id"], required=False)
    if (account is None or account["data"].get("subject") != claims["sub"]
            or account["data"].get("issuer") != claims["iss"]
            or account["data"].get("status") not in ("active", "invited")
            or account["data"].get("role") not in ROLES):
        raise api.APIError(403, "account_unavailable", "This identity has no active website account.")
    if int(claims["iat"]) <= int(account["data"].get("revoked_before", -1)):
        raise api.APIError(401, "session_expired", "Sign in again to continue.")
    if account["data"]["status"] == "invited":
        import accounts
        account = accounts.activate(api, account)
    return {"kind": "human", "staff_id": account["SK"], "subject": claims["sub"],
            "issuer": claims["iss"], "role": account["data"]["role"], "account": account,
            "token_issued_at": int(claims["iat"])}


def producer_route(api, method, path, query):
    """Keep the existing uploader/report clients, with demo-only provenance.

    No shared-token website management, media, snapshot, staff or card writes.
    Real telemetry uses separately provisioned hardware credentials.
    """
    tail = path.removeprefix("/api/")
    collection = tail.split("/")[0]
    allowed = (method == "GET" and (collection in PRODUCER_COLLECTIONS + ("exhibits", "schema",)
               or path in ("/api/analytics/heatmap", "/api/reports/event", "/api/activity")))
    allowed = allowed or method == "POST" and collection in PRODUCER_COLLECTIONS + ("exhibits",) and "/" not in tail
    allowed = allowed or method == "GET" and collection == "commands" and bool(query.get("device_id")) and "/" not in tail
    # Producers re-read one immutable command identity immediately before
    # executing; response filtering still enforces the bound demo device.
    allowed = allowed or method == "GET" and bool(api.re.fullmatch(r"/api/commands/[A-Za-z0-9][A-Za-z0-9_-]{0,63}", path))
    allowed = allowed or method == "POST" and collection == "commands" and (
        path.endswith("/acknowledgements") or path.endswith("/recovery-acknowledgements"))
    if not allowed:
        raise api.APIError(403, "forbidden", "The demo producer credential cannot access website management operations.")
    if query.get("device_id") and not demo_device(query["device_id"]):
        raise api.APIError(403, "forbidden", "This credential is restricted to configured demo devices.")
    if method == "POST" and collection == "commands":
        command_id = path.split("/")[3]
        command = store.get(api, "command", command_id)
        if not demo_device(command["data"]["device_id"]):
            raise api.APIError(403, "forbidden", "This credential is restricted to configured demo devices.")
    return {"kind": "producer", "role": "Demo producer"}


def hardware_token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def authorize_hardware(api, token, method, path, query):
    item = None
    if HARDWARE_TOKEN.fullmatch(token):
        item = store.get(api, HARDWARE_CREDENTIAL_KIND, hardware_token_digest(token), required=False)
    credential = item.get("data") if item else None
    device_id = credential.get("device_id") if isinstance(credential, dict) and credential.get("active") is True else None
    if not isinstance(device_id, str) or not api.IDENTIFIER.fullmatch(device_id) or demo_device(device_id):
        raise api.APIError(401, "unauthorized", "A valid Bearer API token is required.")
    person = {"kind": "hardware", "device_id": device_id}
    if method == "POST" and path in HARDWARE_POST_PATHS:
        return person
    if method == "GET" and path == "/api/commands":
        if (query.get("device_id") == device_id and query.get("type") in HARDWARE_COMMAND_TYPES
                and query.get("status") in ("pending", "recovery_pending")):
            return person
        raise api.APIError(403, "forbidden", "Poll pending or recovery_pending assignment, label or lighting commands for the registered device_id.")
    suffix = {"GET": "", "POST": r"/(?:acknowledgements|recovery-acknowledgements)"}.get(method)
    match = re.fullmatch(r"/api/commands/([A-Za-z0-9][A-Za-z0-9_-]{0,63})" + suffix, path) if suffix is not None else None
    if match:
        command = store.get(api, "command", match[1], required=False)
        if command and command["data"].get("device_id") == device_id and command["data"].get("type") in HARDWARE_COMMAND_TYPES:
            return person
        raise api.APIError(404, "not_found", "Command not found.")
    raise api.APIError(403, "forbidden", "This hardware credential cannot access that operation.")


def authorize_human(api, person, method, path, query):
    role = person["role"]
    admin = role == "Administrator"
    operator = role in ("Administrator", "Operator")
    collection = path.removeprefix("/api/").split("/")[0]
    if collection == "commands" and path.endswith("/recovery-requests") and not operator:
        raise api.APIError(403, "forbidden", "This website role cannot perform that operation.")
    if collection == "commands" and path.endswith("/recovery-requests") and not admin:
        command = store.get(api, "command", path.split("/")[3])
        if command["data"]["type"] == "card":
            raise api.APIError(403, "forbidden", "Administrator permission is required for physical cards.")
    # Human identities cannot submit or amend device evidence, even Admin.
    if method != "GET" and (collection in PRODUCER_COLLECTIONS or path.endswith("/acknowledgements")
                             or path.endswith("/recovery-acknowledgements")):
        raise api.APIError(403, "forbidden", "Human sessions cannot submit producer evidence.")
    if collection in ("staff", "accounts", "access-attempts") and not admin:
        raise api.APIError(403, "forbidden", "Administrator permission is required.")
    if collection == "tag-scans" and not operator:
        raise api.APIError(403, "forbidden", "Operator or Administrator permission is required for exhibit scans.")
    if collection == "tag-scans" and query.get("scan_type") == "staff_card" and not admin:
        raise api.APIError(403, "forbidden", "Administrator permission is required for physical cards.")
    if collection == "workflow-activity" and not admin and query.get("type") == "card":
        raise api.APIError(403, "forbidden", "Administrator permission is required for physical cards.")
    allowed_get = {"exhibits", "readings", "schema", "exhibit-profiles", "showcases", "label-drafts", "commands",
                   "device-status", "analytics", "reports", "activity", "report-snapshots", "media",
                   "events", "proximity-events", "coverage", "display-history", "warnings", "tag-scans", "workflow-activity"}
    if method == "GET" and (collection in allowed_get or admin and collection in ("staff", "accounts", "access-attempts")):
        if collection == "commands" and query.get("type") == "card" and not admin:
            raise api.APIError(403, "forbidden", "Administrator permission is required for physical cards.")
        return
    if collection == "rfid-sessions" and operator and method in ("GET", "POST"):
        return
    if collection == "session" and method in ("GET", "POST"):
        return
    if collection in ("staff", "accounts") and admin and method in ("POST", "PATCH"):
        return
    if operator and collection in ("exhibit-profiles", "showcases", "label-drafts", "commands", "media", "report-snapshots") and method in ("POST", "PATCH", "PUT"):
        return
    raise api.APIError(403, "forbidden", "This website role cannot perform that operation.")


def authorize(api, event, method, path, headers, query):
    if path == "/staff-api" or path.startswith("/staff-api/"):
        person = human(api, event)
        normalized = "/api" + path[len("/staff-api"):]
        authorize_human(api, person, method, normalized, query)
        return normalized, person
    parts = headers.get("authorization", "").split()
    token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if token.startswith(HARDWARE_TOKEN_PREFIX):
        return path, authorize_hardware(api, token, method, path, query)
    api.require_auth(token)
    if enforced():
        return path, producer_route(api, method, path, query)
    return path, {"kind": "legacy", "role": "Legacy test fixture"}


def body(api, data, path):
    person = principal.get()
    if not person or person["kind"] == "legacy":
        return data
    if person["kind"] == "hardware":
        if data.get("source") != "device" or data.get("device_id") != person["device_id"]:
            raise api.APIError(403, "forbidden", "This credential requires source=device and its registered device_id.")
        return data
    if person["kind"] == "producer":
        if path.removeprefix("/api/").split("/")[0] in PRODUCER_COLLECTIONS and data.get("source") != "demo":
            raise api.APIError(403, "forbidden", "This credential is restricted to source=demo.")
        if "source" in data and data["source"] != "demo":
            raise api.APIError(403, "forbidden", "This credential is restricted to source=demo.")
        if data.get("device_id") and not demo_device(data["device_id"]):
            raise api.APIError(403, "forbidden", "This credential is restricted to configured demo devices.")
        if data.get("exhibit_id") and not demo_exhibit(data["exhibit_id"]):
            raise api.APIError(403, "forbidden", "This credential is restricted to configured demo exhibits.")
        return data
    if "actor" in data:
        raise api.APIError(403, "forbidden", "Audit actor comes from the authenticated session.")
    if path == "/api/commands" and data.get("type") == "card" and person["role"] != "Administrator":
        raise api.APIError(403, "forbidden", "Administrator permission is required for physical cards.")
    if path == "/api/commands" or path.endswith("/recovery-requests"):
        return {**data, "actor": person["staff_id"] + ":" + person["subject"]}
    return data


def audit_actions(api):
    person = principal.get()
    if not person or person["kind"] != "human" or person.get("method") == "GET":
        return []
    import uuid
    item = store.make(api, "staff-audit", uuid.uuid4().hex,
                      {"actor": {"staff_id": person["staff_id"], "subject": person["subject"],
                                 "issuer": person["issuer"], "role": person["role"]},
                       "method": person["method"], "path": person["path"]})
    return [store.put_action(api, item), *store.index_actions(api, item)]


def demo_device(device_id):
    prefixes = os.environ.get("DEMO_DEVICE_PREFIXES", "esp32-demo-,ec2-demo-,analytics-,wf-,v04-,demo-").split(",")
    return any(prefix and device_id.startswith(prefix) for prefix in prefixes)


def demo_exhibit(exhibit_id):
    prefixes = os.environ.get("DEMO_EXHIBIT_PREFIXES", "analytics-,wf-,v04-,demo-").split(",")
    return any(prefix and exhibit_id.startswith(prefix) for prefix in prefixes)


def filter_response(api, result):
    person = principal.get()
    # Hardware routes enforce device and command scope before reaching this point.
    if not person or person["kind"] in ("legacy", "hardware") or result.get("statusCode", 500) >= 400:
        return result
    content_type = result.get("headers", {}).get("content-type", "")
    if "application/json" not in content_type:
        return result
    value = json.loads(result["body"])
    if person["kind"] == "human" and "item" in value:
        import staff_reads
        collection = person.get("path", "").removeprefix("/api/").split("/")[0]
        kind = {"commands": "command", "tag-scans": "scan", "workflow-activity": "activity"}.get(collection)
        if kind:
            visible = staff_reads.project(api, kind, value["item"])
            if visible is None:
                raise api.APIError(403, "forbidden", "This website role cannot access that record.")
            value["item"] = visible
    def permitted(item):
        if not isinstance(item, dict):
            return True
        if person["kind"] == "human":
            return person["role"] == "Administrator" or item.get("type") != "card"
        return (item.get("source") in (None, "demo") and
                (not item.get("device_id") or demo_device(item["device_id"])) and
                (not item.get("exhibit_id") or demo_exhibit(item["exhibit_id"])))
    if "item" in value and not permitted(value["item"]):
        raise api.APIError(403, "forbidden", "This credential cannot access that record.")
    if "items" in value:
        value["items"] = [item for item in value["items"] if permitted(item)]
    # Device-wide queries must be restricted before analytics computes totals.
    result["body"] = json.dumps(value, default=api.json_default, allow_nan=False, separators=(",", ":"))
    return result
