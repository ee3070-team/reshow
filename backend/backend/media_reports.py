"""Private verified image assets and durable, coverage-aware report snapshots.

Browser uploads reach a random staging key. Only verified bytes are copied to a
separate immutable final key, so replaying a signed form cannot change an image
already referenced by an exhibit profile. No signed URLs are persisted here.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import analytics
import workflow_store as store

MAX_IMAGE_BYTES = 1_048_576
URL_SECONDS = 300
MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")
SECTIONS = ("history", "activity", "environment", "missing")
MAX_PROFILE_CONTEXT = 100
_s3 = None


def storage(api):
    global _s3
    bucket = os.environ.get("ASSET_BUCKET")
    if not bucket:
        raise api.APIError(503, "service_unavailable", "Asset storage is not configured.")
    if _s3 is None:
        import boto3
        from botocore.config import Config
        _s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    return _s3, bucket


def error_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code")


def media_item(item):
    data = item["data"]
    public = {key: data[key] for key in ("media_id", "content_type", "size_bytes", "status", "sha256") if key in data}
    return {**public, **{key: item[key] for key in ("version", "created_at", "updated_at")}}


def upload_request(api, body):
    api.exact_fields(body, ("media_id", "content_type", "size_bytes"))
    media_id = api.identifier(body["media_id"], "media_id")
    if body["content_type"] not in MEDIA_TYPES:
        raise api.APIError(400, "invalid_payload", "content_type must be image/png, image/jpeg or image/webp.")
    size = api.number(body["size_bytes"], "size_bytes", 1, MAX_IMAGE_BYTES, integer=True)
    identity = {"media_id": media_id, "content_type": body["content_type"], "size_bytes": size}
    item = store.get(api, "media", media_id, required=False)
    created = False
    if item is None:
        data = {**identity, "status": "pending", "staging_key": "media/staging/" + media_id + "/" + uuid.uuid4().hex}
        try:
            item, created = store.create(api, "media", media_id, data)
        except api.APIError as exc:
            if exc.code not in ("id_conflict", "version_conflict"):
                raise
            item = store.get(api, "media", media_id, required=False)
            if item is None:
                raise
    if any(item["data"].get(key) != value for key, value in identity.items()):
        raise api.APIError(409, "id_conflict", "This media_id already belongs to different image metadata.")
    result = {"status": "created" if created else "duplicate", "item": media_item(item)}
    if item["data"]["status"] == "pending":
        client, bucket = storage(api)
        form = client.generate_presigned_post(
            Bucket=bucket, Key=item["data"]["staging_key"],
            Fields={"Content-Type": identity["content_type"], "x-amz-server-side-encryption": "AES256"},
            Conditions=[{"Content-Type": identity["content_type"]}, {"x-amz-server-side-encryption": "AES256"},
                        ["content-length-range", size, size]], ExpiresIn=URL_SECONDS)
        result["upload"] = {**form, "expires_in_seconds": URL_SECONDS}
    return api.response(201 if created else 200, result)


def check_signature(content_type, body):
    if content_type == "image/png":
        return (len(body) >= 33 and body[:8] == b"\x89PNG\r\n\x1a\n" and body[8:16] == b"\x00\x00\x00\rIHDR"
                and int.from_bytes(body[16:20], "big") > 0 and int.from_bytes(body[20:24], "big") > 0)
    if content_type == "image/jpeg":
        return len(body) >= 4 and body[:3] == b"\xff\xd8\xff" and body[-2:] == b"\xff\xd9"
    return (len(body) >= 20 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"
            and body[12:16] in (b"VP8 ", b"VP8L", b"VP8X") and int.from_bytes(body[4:8], "little") == len(body) - 8)


def bounded_object(api, client, bucket, key, maximum, expected_size=None, expected_type=None, expected_hash=None):
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        size = head["ContentLength"]
        if size < 1 or size > maximum or (expected_size is not None and size != expected_size):
            raise api.APIError(400, "invalid_asset", "Stored object size does not match the permitted declared size.")
        if expected_type and head.get("ContentType") != expected_type:
            raise api.APIError(400, "invalid_asset", "Stored Content-Type does not match the declared type.")
        response = client.get_object(Bucket=bucket, Key=key, Range="bytes=0-" + str(maximum), IfMatch=head["ETag"])
        stream = response["Body"]
        try:
            # ETags can stay the same when object metadata changes.
            if expected_type and response.get("ContentType") != expected_type:
                raise api.APIError(400, "invalid_asset", "Stored Content-Type does not match the declared type.")
            body = stream.read(maximum + 1)
        finally:
            stream.close()
        if len(body) != size or len(body) > maximum:
            raise api.APIError(400, "invalid_asset", "Stored object size changed or exceeded the limit.")
        if expected_hash and hashlib.sha256(body).hexdigest() != expected_hash:
            raise api.APIError(503, "asset_integrity_error", "Stored object integrity verification failed.")
        return body
    except api.APIError:
        raise
    except Exception as exc:
        if error_code(exc) in ("404", "NoSuchKey", "NotFound", "403", "AccessDenied"):
            # Without broad ListBucket permission, S3 intentionally reports
            # 403 for a missing key. Do not infer that an upload is present.
            raise api.APIError(409, "upload_incomplete", "The upload is not available for verification. Upload the image before completing this request.") from None
        if error_code(exc) in ("412", "PreconditionFailed"):
            raise api.APIError(409, "upload_changed", "The upload changed while being verified; retry completion.") from None
        raise


def immutable_object(api, client, bucket, key, body, content_type):
    """Conditional object creation; a deterministic digest key is never replaced."""
    digest = hashlib.sha256(body).hexdigest()
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type,
                          ServerSideEncryption="AES256", Metadata={"sha256": digest}, IfNoneMatch="*")
    except Exception as exc:
        if error_code(exc) not in ("412", "PreconditionFailed", "ConditionalRequestConflict"):
            raise
        # A prior successful attempt may have stored S3 before the metadata
        # transaction failed. Verify bytes instead of assuming it is ours.
        bounded_object(api, client, bucket, key, len(body), expected_size=len(body),
                       expected_type=content_type, expected_hash=digest)
    return digest


def complete_media(api, media_id):
    item = store.get(api, "media", media_id)
    if item["data"]["status"] == "ready":
        return api.response(200, {"item": media_item(item)})
    data = item["data"]
    client, bucket = storage(api)
    body = bounded_object(api, client, bucket, data["staging_key"], MAX_IMAGE_BYTES,
                          expected_size=data["size_bytes"], expected_type=data["content_type"])
    if not check_signature(data["content_type"], body):
        raise api.APIError(400, "invalid_asset", "The object signature does not match a supported declared image type.")
    digest = hashlib.sha256(body).hexdigest()
    key = "media/final/" + media_id + "/" + digest
    immutable_object(api, client, bucket, key, body, data["content_type"])
    ready = {**data, "status": "ready", "sha256": digest, "final_key": key}
    try:
        item = store.replace(api, "media", media_id, ready, item["version"])
    except api.APIError as exc:
        if exc.status != 409:
            raise
        winner = store.get(api, "media", media_id)
        if winner["data"].get("status") != "ready" or winner["data"].get("sha256") != digest:
            raise
        item = winner
    return api.response(200, {"item": media_item(item)})


def get_media(api, media_id):
    item = store.get(api, "media", media_id)
    result = {"item": media_item(item)}
    if item["data"]["status"] == "ready":
        client, bucket = storage(api)
        result["download"] = {"url": client.generate_presigned_url("get_object", Params={
            "Bucket": bucket, "Key": item["data"]["final_key"]}, ExpiresIn=URL_SECONDS), "expires_in_seconds": URL_SECONDS}
    return api.response(200, result)


def report_identity(api, body):
    allowed = {"report_id", "title", "event_id", "device_id", "start", "end", "interval_minutes", "timezone", "sections", "exhibit_id"}
    if set(body) - allowed:
        raise api.APIError(400, "invalid_payload", "Unsupported report snapshot field.")
    report_id = api.identifier(body.get("report_id"), "report_id")
    title = api.text(body.get("title", "Event report"), "title", 120)
    sections = body.get("sections", list(SECTIONS))
    if (not isinstance(sections, list) or not sections or len(sections) > 4
            or any(not isinstance(value, str) or value not in SECTIONS for value in sections) or len(set(sections)) != len(sections)):
        raise api.APIError(400, "invalid_payload", "sections must contain unique history, activity, environment and/or missing values.")
    sections = sorted(set(sections) | {"missing"})
    minutes = api.number(body.get("interval_minutes", 15), "interval_minutes", 1, 1440, integer=True)
    zone = body.get("timezone", "Asia/Hong_Kong")
    if not isinstance(zone, str) or len(zone) > 100:
        raise api.APIError(400, "invalid_payload", "timezone must name an available IANA time zone.")
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise api.APIError(400, "invalid_payload", "timezone must name an available IANA time zone.") from None
    query = {"interval_minutes": str(minutes), "timezone": zone}
    if "exhibit_id" in body:
        query["exhibit_id"] = api.identifier(body["exhibit_id"], "exhibit_id")
    if "event_id" in body:
        if any(key in body for key in ("device_id", "start", "end")):
            raise api.APIError(400, "invalid_payload", "Use event_id OR device_id,start,end.")
        query["event_id"] = api.identifier(body["event_id"], "event_id")
    else:
        query["device_id"] = api.identifier(body.get("device_id"), "device_id")
        left, right = [analytics.parse_time(api, body.get(key), key) for key in ("start", "end")]
        analytics.validate_window(api, left, right, report=True)
        query.update(start=api.timestamp(left), end=api.timestamp(right))
    return report_id, {"title": title, "query": query, "sections": sections}


def frozen_profiles(api, details, captured_at, exhibit_id=None):
    ids = sorted({row["exhibit_id"] for key in ("display_history", "environmental_warnings") for row in details[key]}
                 | ({exhibit_id} if exhibit_id else set()))
    if len(ids) > MAX_PROFILE_CONTEXT:
        raise api.APIError(413, "report_too_large", "Snapshot profile context exceeds 100 exhibits; narrow the selected date range.")
    exhibits = []
    for exhibit_id in ids:
        item = store.get(api, "exhibit-profile", exhibit_id, required=False)
        profile = store.public(item) if item else None
        if profile is None:
            try:
                profile = api.public_record(api.read_record("exhibit", exhibit_id))
            except api.APIError as exc:
                if exc.status != 404:
                    raise
        exhibits.append({"exhibit_id": exhibit_id, "profile": profile})
    return {"captured_at": captured_at, "historical_settings_known": False,
            "note": "Profiles are current at capture; they are not historical application evidence.", "exhibits": exhibits}


def snapshot_item(item):
    data = item["data"]
    return {key: data[key] for key in ("report_id", "title", "device_id", "start", "end", "sections", "captured_at", "filter", "evaluation", "exhibit_id") if key in data} | {
        key: item[key] for key in ("version", "created_at", "updated_at")}


def create_snapshot(api, body):
    report_id, identity = report_identity(api, body)
    existing = store.get(api, "report-snapshot", report_id, required=False)
    if existing:
        if existing["data"]["request"] != identity:
            raise api.APIError(409, "id_conflict", "This report_id already belongs to a different capture request.")
        return api.response(200, {"status": "duplicate", "item": snapshot_item(existing)})
    config = analytics.configuration(api, identity["query"])
    device, left, right, event, interval, zone_name, zone, _format = config
    cutoff = api.now_utc()
    items = analytics.read_timeline(api, device) if left < cutoff else []
    base, details = analytics.calculate(api, items, device, left, right, event, interval, zone_name, zone,
                                      exhibit_id=identity["query"].get("exhibit_id"), captured_at=cutoff)
    captured = api.timestamp(cutoff)
    payload = {"report_id": report_id, "title": identity["title"], "captured_at": captured,
               "sections": identity["sections"], "report": {**base, **details},
               "profile_context": frozen_profiles(api, details, captured, identity["query"].get("exhibit_id"))}
    # Validate both the stored object size and its future full JSON response.
    analytics.bounded_response(api, payload)
    raw = json.dumps(payload, default=api.json_default, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > analytics.MAX_RESPONSE_BYTES:
        raise api.APIError(413, "report_too_large", "Snapshot exceeds the 4 MB storage limit; narrow the date range.")
    digest = hashlib.sha256(raw).hexdigest()
    key = "report-snapshots/" + report_id + "/" + digest + ".json"
    client, bucket = storage(api)
    immutable_object(api, client, bucket, key, raw, "application/json")
    metadata = {"report_id": report_id, "title": identity["title"], "device_id": device,
                "start": api.timestamp(left), "end": api.timestamp(right), "sections": identity["sections"],
                "filter": base["filter"],
                # Analytics JSON uses fractional seconds; DynamoDB's native
                # resource serializer requires Decimal rather than float.
                "evaluation": json.loads(json.dumps(base["evaluation"], allow_nan=False), parse_float=Decimal),
                "captured_at": captured, "request": identity, "object_key": key, "size_bytes": len(raw), "sha256": digest}
    if "exhibit_id" in identity["query"]:
        metadata["exhibit_id"] = identity["query"]["exhibit_id"]
    try:
        item, created = store.create(api, "report-snapshot", report_id, metadata)
    except api.APIError as exc:
        if exc.status != 409:
            raise
        item = store.get(api, "report-snapshot", report_id)
        if item["data"]["request"] != identity:
            raise
        created = False
    return api.response(201 if created else 200, {"status": "created" if created else "duplicate", "item": snapshot_item(item)})


def selected_payload(payload):
    """Keep all completeness caveats even when a presentation section is hidden."""
    result = dict(payload)
    report = dict(payload["report"])
    sections = payload["sections"]
    if "activity" not in sections:
        report.pop("proximity_events", None)
        report.pop("buckets", None)
    if "history" not in sections:
        report.pop("display_history", None)
    if "environment" not in sections:
        report.pop("environmental_warnings", None)
    result["report"] = report
    return result


def get_snapshot(api, report_id, output_format):
    item = store.get(api, "report-snapshot", report_id)
    data = item["data"]
    client, bucket = storage(api)
    try:
        raw = bounded_object(api, client, bucket, data["object_key"], analytics.MAX_RESPONSE_BYTES,
                             expected_size=data["size_bytes"], expected_type="application/json", expected_hash=data["sha256"])
    except api.APIError as exc:
        if exc.status not in (400, 409):
            raise
        raise api.APIError(503, "snapshot_unavailable", "The saved snapshot object is missing or failed integrity validation.") from None
    payload = selected_payload(json.loads(raw))
    if output_format == "json":
        return analytics.bounded_response(api, payload)
    base = {**payload["report"], "buckets": payload["report"].get("buckets", [])}
    # Existing CSV serialization protects free text against spreadsheet formula
    # execution; the captured snapshot metadata stays in its summary detail.
    base["data_quality"] = {**base["data_quality"], "snapshot": {key: payload[key] for key in
                            ("report_id", "title", "captured_at", "sections", "profile_context")}}
    details = {key: base[key] for key in ("proximity_events", "display_history", "environmental_warnings", "coverage_records") if key in base}
    return analytics.csv_response(api, base, details)


def activity_query(api, query):
    if set(query) - {"device_id", "start_date", "end_date", "timezone", "interval_minutes", "format", "exhibit_id"}:
        raise api.APIError(400, "invalid_query", "Unsupported activity query parameter.")
    zone_name = query.get("timezone", "Asia/Hong_Kong")
    if not isinstance(zone_name, str) or len(zone_name) > 100:
        raise api.APIError(400, "invalid_query", "timezone must name an available IANA time zone.")
    try:
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise api.APIError(400, "invalid_query", "timezone must name an available IANA time zone.") from None
    dates = []
    for field in ("start_date", "end_date"):
        value = query.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise api.APIError(400, "invalid_query", field + " must use YYYY-MM-DD.")
        try:
            dates.append(date.fromisoformat(value))
        except ValueError:
            raise api.APIError(400, "invalid_query", field + " must be a valid calendar date.") from None
    if dates[0] > dates[1] or (dates[1] - dates[0]).days >= 31:
        raise api.APIError(400, "invalid_query", "Select 1-31 inclusive local dates, with start_date no later than end_date.")
    try:
        left = datetime.combine(dates[0], time.min, tzinfo=zone).astimezone(timezone.utc)
        right = datetime.combine(dates[1] + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise api.APIError(400, "invalid_query", "The selected date boundary cannot be represented.") from None
    translated = {key: query[key] for key in ("device_id", "interval_minutes", "format", "exhibit_id") if key in query}
    translated.update(start=api.timestamp(left), end=api.timestamp(right), timezone=zone_name)
    config = analytics.configuration(api, translated)
    return config, {"start_date": query["start_date"], "end_date": query["end_date"], "timezone": zone_name,
                    "end_date_inclusive": True, "semantics": "Local midnight through midnight after end_date, converted to half-open UTC boundaries."}


def route(api, method, path, query, event, headers):
    if method == "POST" and path == "/api/media/upload-requests":
        if query:
            raise api.APIError(400, "invalid_query", "This route does not accept query parameters.")
        return upload_request(api, api.parse_body(event, headers))
    if path.startswith("/api/media/"):
        tail = path[len("/api/media/"):]
        complete = tail.endswith("/complete")
        media_id = api.identifier(tail[:-len("/complete")] if complete else tail, "media_id")
        if query:
            raise api.APIError(400, "invalid_query", "This route does not accept query parameters.")
        if method != ("POST" if complete else "GET"):
            raise api.APIError(405, "method_not_allowed", "Use POST to complete media or GET to retrieve it.")
        if complete:
            api.exact_fields(api.parse_body(event, headers), ())
            return complete_media(api, media_id)
        return get_media(api, media_id)
    if path == "/api/report-snapshots":
        if method == "POST":
            if query:
                raise api.APIError(400, "invalid_query", "POST does not accept query parameters.")
            return create_snapshot(api, api.parse_body(event, headers))
        if method == "GET":
            if set(query) - {"device_id", "exhibit_id", "limit", "cursor"}:
                raise api.APIError(400, "invalid_query", "Use only device_id, exhibit_id, limit and cursor to list saved reports.")
            page = store.list_records(api, "report-snapshot", query)
            # list_records returns public records; never expose S3 keys/request
            # internals from those records in the metadata-only list.
            page["items"] = [{key: value for key, value in row.items() if key in (
                "report_id", "title", "device_id", "start", "end", "sections", "captured_at", "version", "created_at", "updated_at", "filter", "evaluation", "exhibit_id")}
                for row in page["items"]]
            return api.response(200, page)
        raise api.APIError(405, "method_not_allowed", "This route accepts GET or POST.")
    if path.startswith("/api/report-snapshots/"):
        if method != "GET":
            raise api.APIError(405, "method_not_allowed", "This route accepts GET.")
        if set(query) - {"format"} or query.get("format", "json") not in ("json", "csv"):
            raise api.APIError(400, "invalid_query", "Only format=json or format=csv is accepted.")
        return get_snapshot(api, api.identifier(path[len("/api/report-snapshots/" ):], "report_id"), query.get("format", "json"))
    if path == "/api/activity":
        if method != "GET":
            raise api.APIError(405, "method_not_allowed", "This route accepts GET.")
        config, selection = activity_query(api, query)
        device, left, right, selected, interval, zone_name, zone, output_format = config
        cutoff = api.now_utc()
        items = analytics.read_timeline(api, device) if left < cutoff else []
        base, details = analytics.calculate(api, items, device, left, right, selected, interval, zone_name, zone,
                                          exhibit_id=query.get("exhibit_id"), captured_at=cutoff)
        base["date_selection"] = selection
        return analytics.csv_response(api, base, details) if output_format == "csv" else analytics.bounded_response(api, {**base, **details})
    return None
