"""ReShow demonstration API: API Gateway HTTP API -> Lambda -> DynamoDB.

One immutable record per POST. All protected routes require a Bearer API token.
The device query uses an eventually consistent index; exact GET is strongly
consistent and is the required verification step after an upload.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import uuid
import auth
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


LOG = logging.getLogger(__name__)
MAX_BODY_BYTES = 16_384
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_table = None
CORS_HEADERS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, PATCH, PUT, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type, If-Match",
    "access-control-expose-headers": "Location, Content-Disposition, ETag",
    "access-control-max-age": "600",
}
SWAGGER_ASSETS = {
    "/assets/swagger-ui-5.34.0-bundle.js": ("swagger-ui-5.34.0-bundle.js", "application/javascript; charset=utf-8"),
    "/assets/swagger-ui-5.34.0.css": ("swagger-ui-5.34.0.css", "text/css; charset=utf-8"),
}


class APIError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def now_utc():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError("Unsupported JSON value")


def response(status, data, extra_headers=None):
    headers = {
        **CORS_HEADERS,
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
    }
    headers.update(extra_headers or {})
    return {"statusCode": status, "headers": headers,
            "body": json.dumps(data, default=json_default, allow_nan=False, separators=(",", ":"))}


def get_table():
    global _table
    if _table is None:
        import boto3  # Included in the managed Lambda Python runtime.
        table_name = os.environ.get("TABLE_NAME")
        if not table_name:
            raise APIError(503, "service_unavailable", "Storage is not configured.")
        _table = boto3.resource("dynamodb").Table(table_name)
    return _table


def require_auth(token):
    expected = os.environ.get("API_TOKEN", "")
    if len(expected) < 32:
        raise APIError(503, "service_unavailable", "Authentication is not configured.")
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise APIError(401, "unauthorized", "A valid Bearer API token is required.")


def reject_constant(_value):
    raise ValueError("Non-finite number")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_body(event, headers):
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise APIError(415, "unsupported_media_type", "Use Content-Type: application/json.")
    raw = event.get("body") or ""
    if not isinstance(raw, str) or len(raw) > MAX_BODY_BYTES * 2:
        raise APIError(413, "payload_too_large", "JSON body must be at most 16384 bytes.")
    try:
        raw_bytes = base64.b64decode(raw, validate=True) if event.get("isBase64Encoded") else raw.encode("utf-8")
        if len(raw_bytes) > MAX_BODY_BYTES:
            raise APIError(413, "payload_too_large", "JSON body must be at most 16384 bytes.")
        data = json.loads(raw_bytes.decode("utf-8"), parse_float=Decimal,
                          parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise APIError(400, "invalid_json", "Provide a valid UTF-8 JSON object with unique keys.")
    if not isinstance(data, dict):
        raise APIError(400, "invalid_payload", "Provide one JSON object per request; batches are not supported.")
    person = auth.principal.get()
    return auth.body(sys.modules[__name__], data, person.get("path", "") if person else "")


def identifier(value, field):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise APIError(400, "invalid_payload", field + " must be 1-64 letters, digits, underscores or hyphens, starting with a letter or digit.")
    return value


def text(value, field, maximum, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise APIError(400, "invalid_payload", field + " must be a string of " + ("0" if allow_empty else "1") + "-" + str(maximum) + " characters.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise APIError(400, "invalid_payload", field + " contains unsupported control characters.")
    return value


def number(value, field, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise APIError(400, "invalid_payload", field + " must be a JSON number.")
    decimal = Decimal(value)
    if not decimal.is_finite() or not Decimal(str(minimum)) <= decimal <= Decimal(str(maximum)):
        raise APIError(400, "invalid_payload", field + " must be between " + str(minimum) + " and " + str(maximum) + ".")
    if integer and decimal != decimal.to_integral_value():
        raise APIError(400, "invalid_payload", field + " must be an integer.")
    # DynamoDB supports 38 significant digits. A narrow precision bound also
    # avoids accepted values that cannot be reliably represented in the demo UI.
    if len(decimal.as_tuple().digits) > 12 or decimal.as_tuple().exponent < -6:
        raise APIError(400, "invalid_payload", field + " supports at most six decimal places and twelve significant digits.")
    return int(decimal) if integer else decimal


def exact_fields(data, required):
    missing, unknown = set(required) - set(data), set(data) - set(required)
    if missing:
        raise APIError(400, "invalid_payload", "Missing fields: " + ", ".join(sorted(missing)) + ".")
    if unknown:
        raise APIError(400, "invalid_payload", "Unknown fields: " + ", ".join(sorted(unknown)) + ".")


def validate_exhibit(data):
    exact_fields(data, ("exhibit_id", "name", "description", "tag_id", "temperature_min_c",
                        "temperature_max_c", "brightness_min_raw", "brightness_max_raw"))
    result = {
        "exhibit_id": identifier(data["exhibit_id"], "exhibit_id"),
        "name": text(data["name"], "name", 120),
        "description": text(data["description"], "description", 2000, allow_empty=True),
        "tag_id": identifier(data["tag_id"], "tag_id"),
        "temperature_min_c": number(data["temperature_min_c"], "temperature_min_c", -40, 125),
        "temperature_max_c": number(data["temperature_max_c"], "temperature_max_c", -40, 125),
        "brightness_min_raw": number(data["brightness_min_raw"], "brightness_min_raw", 0, 4095, integer=True),
        "brightness_max_raw": number(data["brightness_max_raw"], "brightness_max_raw", 0, 4095, integer=True),
    }
    for lower, upper in (("temperature_min_c", "temperature_max_c"), ("brightness_min_raw", "brightness_max_raw")):
        if result[lower] > result[upper]:
            raise APIError(400, "invalid_payload", lower + " must not exceed " + upper + ".")
    return result


def validate_reading(data):
    exact_fields(data, ("reading_id", "device_id", "exhibit_id", "recorded_at", "temperature_c",
                        "brightness_raw", "occupied", "distance_cm", "source"))
    result = {key: identifier(data[key], key) for key in ("reading_id", "device_id", "exhibit_id")}
    value = data["recorded_at"]
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise APIError(400, "invalid_payload", "recorded_at must be an RFC3339 UTC timestamp ending in Z.")
    try:
        recorded = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise APIError(400, "invalid_payload", "recorded_at is not a valid date and time.")
    if recorded > now_utc() + timedelta(minutes=5):
        raise APIError(400, "invalid_payload", "recorded_at cannot be more than five minutes in the future.")
    result["recorded_at"] = timestamp(recorded)
    result["temperature_c"] = number(data["temperature_c"], "temperature_c", -40, 125)
    result["brightness_raw"] = number(data["brightness_raw"], "brightness_raw", 0, 4095, integer=True)
    if not isinstance(data["occupied"], bool):
        raise APIError(400, "invalid_payload", "occupied must be a boolean.")
    result["occupied"] = data["occupied"]
    result["distance_cm"] = None if data["distance_cm"] is None else number(data["distance_cm"], "distance_cm", 0, 1000)
    if data["source"] not in ("demo", "device"):
        raise APIError(400, "invalid_payload", "source must be demo or device.")
    result["source"] = data["source"]
    return result


def record_key(entity, record_id):
    return {"PK": entity.upper() + "#" + record_id, "SK": "META"}


def read_record(entity, record_id):
    result = get_table().get_item(Key=record_key(entity, record_id), ConsistentRead=True)
    item = result.get("Item")
    if not item:
        raise APIError(404, "not_found", entity.capitalize() + " was not found.")
    return item


def public_record(item):
    return {**item["data"], "received_at": item["received_at"]}


def create_record(entity, data):
    record_id = data[entity + "_id"]
    item = {**record_key(entity, record_id), "entity_type": entity,
            "data": data, "received_at": timestamp(now_utc())}
    if entity == "reading":
        # Exhibits are immutable and this API has no deletion route, so an
        # existing profile cannot disappear between this read and the write.
        read_record("exhibit", data["exhibit_id"])
        item.update(GSI1PK="DEVICE#" + data["device_id"],
                    GSI1SK=data["recorded_at"] + "#" + record_id)
    try:
        get_table().put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code != "ConditionalCheckFailedException":
            raise
        existing = read_record(entity, record_id)
        if existing["data"] != data:
            raise APIError(409, "id_conflict", "This identifier already belongs to a different payload; stored data was not changed.")
        return response(200, {"status": "duplicate", "item": public_record(existing)})
    return response(201, {"status": "created", "item": public_record(item)},
                    {"location": "/api/" + ("readings" if entity == "reading" else "exhibits") + "/" + record_id})


def schema():
    return {
        "service": "ReShow EE3070 backend",
        "version": "0.4.1",
        "authentication": "Cognito access tokens with reshow/staff scope authorize /staff-api routes under current account roles. The private shared token is restricted to demo producers on selected /api routes. Device-scoped hardware tokens authorize only POST /api/device-status and POST /api/readings with source=device and the matching device_id. Docs, health and /auth/config are public.",
        "upload": "POST /api/readings accepts one JSON object per request (no batch). Create the exhibit first.",
        "reading_example": {"reading_id": str(uuid.uuid4()), "device_id": "esp32-demo-01", "exhibit_id": "demo-artifact-01",
                            "recorded_at": timestamp(now_utc()), "temperature_c": 23.5, "brightness_raw": 1850,
                            "occupied": True, "distance_cm": 65, "source": "demo"},
        "exhibit_example": {"exhibit_id": "demo-artifact-01", "name": "ReShow demonstration artifact",
                            "description": "Synthetic demonstration profile.", "tag_id": "DEMO-TAG-001",
                            "temperature_min_c": 18, "temperature_max_c": 28,
                            "brightness_min_raw": 600, "brightness_max_raw": 3000},
        "limits": {"body_bytes": MAX_BODY_BYTES, "identifier_characters": 64, "temperature_c": [-40, 125],
                   "brightness_raw": [0, 4095], "brightness_unit": "relative ADC value; not calibrated lux",
                   "distance_cm": [0, 1000], "distance_unknown": None, "query_limit": [1, 100],
                   "source": ["demo", "device"], "unknown_fields": "rejected", "recorded_at": "RFC3339 UTC, Z suffix; at most 5 minutes in future"},
        "readback": "GET /api/readings/{reading_id} is strongly consistent. GET /api/readings?device_id=...&limit=20 uses an eventually consistent index, latest recorded_at first, and exposes truncated when more exist.",
        "idempotency": "Repeat the same ID and normalized payload: 200 duplicate. Different payload with same ID: 409, no overwrite.",
        "privacy": "No faces, voices, visitor identifiers or unique visitor counts are collected. occupied is detection-zone activity, not a visitor count.",
        "scope": "Immutable demo ingestion and device-scoped hardware status and reading ingestion, versioned catalogs, Cognito staff login with current application role enforcement, device-bound commands and fenced recovery, explicit completed intervals, exhibit-attributed analytics and immutable snapshots. Website accounts and physical card access are independent. Producer receipts do not prove physical execution; hardware wiring and local warning detection remain producer responsibilities.",
        "management": "See /staff-api/exhibit-profiles, /staff-api/staff, /staff-api/showcases, /staff-api/label-drafts, /staff-api/commands and /staff-api/workflow-activity. PATCH and draft PUT require If-Match versions. Legacy /api/exhibits stays immutable; management brightness is explicitly relative percent (0-100), linearly scaled only for the initial raw snapshot.",
        "analytics": "GET heatmap, event report and local-date activity support optional exhibit_id using unambiguous completed display intervals. Results retain requested and captured effective ranges, future/unknown/partial completeness and JSON/CSV filters. Staff reads use /staff-api; /api analytics remains restricted to demo producer access. See /openapi.json.",
    }


def html_response():
    html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    script_hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(value.encode()).digest()).decode() + "'"
                     for value in re.findall(r"<script>(.*?)</script>", html, flags=re.S)]
    return {"statusCode": 200, "headers": {
        **CORS_HEADERS,
        "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer", "x-frame-options": "DENY",
        "content-security-policy": "default-src 'none'; script-src 'self' " + " ".join(script_hashes) + "; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    }, "body": html}


def asset_response(path):
    filename, content_type = SWAGGER_ASSETS[path]
    return {"statusCode": 200, "headers": {
        **CORS_HEADERS, "content-type": content_type,
        "cache-control": "public, max-age=86400, immutable", "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
    }, "body": Path(__file__).with_name("assets").joinpath(filename).read_text(encoding="utf-8")}


def route(event):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    headers = {str(key).lower(): str(value) for key, value in (event.get("headers") or {}).items()}
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": {**CORS_HEADERS, "cache-control": "no-store"}, "body": ""}
    if method == "GET" and path in ("/", "/docs", "/docs/"):
        return html_response()
    if method == "GET" and path == "/openapi.json":
        return response(200, json.loads(Path(__file__).with_name("openapi.json").read_text(encoding="utf-8")))
    if method == "GET" and path in SWAGGER_ASSETS:
        return asset_response(path)
    if method == "GET" and path == "/api/health":
        return response(200, {"status": "ok", "service": "reshow-demo-api", "time": timestamp(now_utc()),
                              "check": "Lambda handler running; this endpoint does not test database access"})
    if method == "GET" and path == "/auth/config":
        return response(200, auth.config(sys.modules[__name__]))
    query = event.get("queryStringParameters") or {}
    path, person = auth.authorize(sys.modules[__name__], event, method, path, headers, query)
    person.update(path=path, method=method)
    auth.principal.set(person)
    if method == "GET" and path == "/api/schema":
        return response(200, schema())
    import accounts
    account_result = accounts.route(sys.modules[__name__], method, path, query, event, headers)
    if account_result is not None:
        return account_result
    import rfid
    import workflows
    import media_reports
    rfid_result = rfid.route(sys.modules[__name__], method, path, query, event, headers)
    if rfid_result is not None:
        return rfid_result
    media_result = media_reports.route(sys.modules[__name__], method, path, query, event, headers)
    if media_result is not None:
        return media_result
    workflow_result = workflows.route(sys.modules[__name__], method, path, query, event, headers)
    if workflow_result is not None:
        return workflow_result
    import analytics
    analytics_result = analytics.route(sys.modules[__name__], method, path, query, event, headers)
    if analytics_result is not None:
        return analytics_result
    if path == "/api/readings" and method == "GET":
        if set(query) - {"device_id", "limit"}:
            raise APIError(400, "invalid_query", "Only device_id and limit query parameters are supported.")
        device_id = identifier(query.get("device_id"), "device_id")
        raw_limit = query.get("limit", "20")
        if not isinstance(raw_limit, str) or not re.fullmatch(r"[0-9]{1,3}", raw_limit) or not 1 <= int(raw_limit) <= 100:
            raise APIError(400, "invalid_query", "limit must be an integer between 1 and 100.")
        result = get_table().query(IndexName="device-readings-index", KeyConditionExpression="GSI1PK = :device",
                                   ExpressionAttributeValues={":device": "DEVICE#" + device_id},
                                   ScanIndexForward=False, Limit=int(raw_limit))
        return response(200, {"items": [public_record(item) for item in result.get("Items", [])],
                              "consistency": "eventual", "truncated": bool(result.get("LastEvaluatedKey"))})
    for plural, entity, validator in (("readings", "reading", validate_reading), ("exhibits", "exhibit", validate_exhibit)):
        if path == "/api/" + plural:
            if method != "POST":
                raise APIError(405, "method_not_allowed", "This route accepts POST" + (" or GET." if entity == "reading" else "."))
            if query:
                raise APIError(400, "invalid_query", "POST routes do not accept query parameters.")
            return create_record(entity, validator(parse_body(event, headers)))
        prefix = "/api/" + plural + "/"
        if path.startswith(prefix):
            if method != "GET":
                raise APIError(405, "method_not_allowed", "This route accepts GET.")
            if query:
                raise APIError(400, "invalid_query", "Exact GET routes do not accept query parameters.")
            record_id = identifier(path[len(prefix):], entity + "_id")
            return response(200, {"item": public_record(read_record(entity, record_id)), "consistency": "strong"})
    raise APIError(404, "not_found", "Route was not found.")


def lambda_handler(event, context):
    reset = auth.principal.set(None)
    try:
        return auth.filter_response(sys.modules[__name__], route(event))
    except APIError as exc:
        headers = {"www-authenticate": "Bearer"} if exc.status == 401 else None
        return response(exc.status, {"error": {"code": exc.code, "message": exc.message}}, headers)
    except Exception as exc:
        # Never log request headers/bodies, credentials, or AWS exception text.
        LOG.error("Request failed: exception_type=%s request_id=%s", type(exc).__name__,
                  getattr(context, "aws_request_id", "unavailable"))
        message = "The service is temporarily unavailable. Retry the same reading_id to avoid duplicates."
        if str(event.get("rawPath", "")).startswith("/staff-api/media/"):
            message = "The media service is temporarily unavailable. Retry using the same media_id."
        return response(503, {"error": {"code": "service_unavailable", "message": message}})
    finally:
        auth.principal.reset(reset)
