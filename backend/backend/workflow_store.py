"""Versioned workflow records and bounded indexed queries on the existing table.

The DynamoDB *resource* client's transaction hooks serialize native values.
No Scan, UpdateItem or DeleteItem is needed. Released uniqueness reservations
are retained and can only be reused through a conditional versioned write.
"""
import base64
import json
import re


def get(api, kind, record_id, required=True):
    item = api.get_table().get_item(Key={"PK": "WF#" + kind, "SK": record_id}, ConsistentRead=True).get("Item")
    if item is None and required:
        raise api.APIError(404, "not_found", kind + " was not found.")
    return item


def public(item):
    return {**item["data"], "version": item["version"], "created_at": item["created_at"], "updated_at": item["updated_at"]}


def make(api, kind, record_id, data, existing=None):
    at = api.timestamp(api.now_utc())
    return {"PK": "WF#" + kind, "SK": record_id, "data": data,
            "version": int(existing["version"]) + 1 if existing else 1,
            "created_at": existing["created_at"] if existing else at, "updated_at": at,
            "create_data": existing.get("create_data", existing["data"]) if existing else data}


def put_action(api, item, existing=None):
    put = {"TableName": api.get_table().name, "Item": item}
    if existing is None:
        put["ConditionExpression"] = "attribute_not_exists(PK)"
    else:
        put.update(ConditionExpression="#version = :version", ExpressionAttributeNames={"#version": "version"},
                   ExpressionAttributeValues={":version": existing["version"]})
    return {"Put": put}


def check_version_action(api, kind, record_id, existing):
    check = {"TableName": api.get_table().name, "Key": {"PK": "WF#" + kind, "SK": record_id}}
    if existing is None:
        check["ConditionExpression"] = "attribute_not_exists(PK)"
    else:
        check.update(ConditionExpression="#version = :version", ExpressionAttributeNames={"#version": "version"},
                     ExpressionAttributeValues={":version": existing["version"]})
    return {"ConditionCheck": check}


def index_actions(api, item, index_kind=None, at=None):
    """Immutable references; dereference canonical state so lists never go stale."""
    kind = item["PK"][3:]
    index_kind = index_kind or kind
    at = at or item["data"].get("recorded_at") or item["created_at"]
    contexts = ["all"]
    for field in ("device_id", "exhibit_id"):
        if item["data"].get(field):
            contexts.append(field + "#" + item["data"][field])
    return [put_action(api, {"PK": "WFIDX#" + index_kind + "#" + context, "SK": at + "#" + item["SK"],
                             "ref_kind": kind, "ref_id": item["SK"]}) for context in contexts]


def transact(api, actions):
    # Verified staff metadata joins the same atomic write. Legacy/shared-token
    # producer calls contribute no staff audit; the helper never stores bodies.
    auth = getattr(api, "auth", None)
    actions = list(actions) + (auth.audit_actions(api) if auth is not None else [])
    if not actions or len(actions) > 100:
        raise ValueError("Transaction must contain 1-100 actions")
    records = [a["Put"]["Item"] if "Put" in a else a["ConditionCheck"]["Key"] for a in actions]
    keys = [(record["PK"], record["SK"]) for record in records]
    if len(set(keys)) != len(keys):
        raise ValueError("A workflow transaction cannot reference a key twice")
    try:
        api.get_table().meta.client.transact_write_items(TransactItems=actions)
    except Exception as exc:
        details = getattr(exc, "response", {})
        code = details.get("Error", {}).get("Code")
        reasons = details.get("CancellationReasons", [])
        if code == "ConditionalCheckFailedException" or (code == "TransactionCanceledException" and
                any(r.get("Code") == "ConditionalCheckFailed" for r in reasons) and
                all(r.get("Code") in (None, "None", "ConditionalCheckFailed") for r in reasons)):
            raise api.APIError(409, "version_conflict", "A record, unique value or pending command changed. Read current state and retry.") from None
        raise


def create(api, kind, record_id, data):
    previous = get(api, kind, record_id, required=False)
    if previous:
        if previous.get("create_data", previous["data"]) != data:
            raise api.APIError(409, "id_conflict", "This identifier already belongs to a different create payload.")
        return previous, False
    item = make(api, kind, record_id, data)
    transact(api, [put_action(api, item), *index_actions(api, item)])
    return item, True


def replace(api, kind, record_id, data, expected_version):
    previous = get(api, kind, record_id)
    if int(previous["version"]) != expected_version:
        raise api.APIError(409, "version_conflict", "If-Match does not match the current version.")
    item = make(api, kind, record_id, data, previous)
    transact(api, [put_action(api, item, previous)])
    return item


def list_records(api, kind, query, project=None, cursor_codec=None):
    """Query one bounded page; filters never turn this into an unbounded scan."""
    raw_limit = str(query.get("limit", "20"))
    if not re.fullmatch(r"[0-9]{1,3}", raw_limit) or not 1 <= int(raw_limit) <= 100:
        raise api.APIError(400, "invalid_query", "limit must be an integer between 1 and 100.")
    context = "all"
    for field in ("device_id", "exhibit_id"):
        if field in query:
            api.identifier(query[field], field)
            if context == "all":
                context = field + "#" + query[field]
    partition = "WFIDX#" + kind + "#" + context
    values = {":pk": partition}
    expression = "PK = :pk"
    if query.get("start") or query.get("end"):
        # Parsers in the route module canonicalize the timestamps first.
        start, end = query.get("start", "0000"), query.get("end", "9999")
        if start >= end:
            raise api.APIError(400, "invalid_query", "start must be before end.")
        expression += " AND SK BETWEEN :start AND :end"
        values.update({":start": start, ":end": end})
    kwargs = dict(KeyConditionExpression=expression, ExpressionAttributeValues=values,
                  ConsistentRead=True, ScanIndexForward=False, Limit=int(raw_limit))
    cursor = query.get("cursor")
    if cursor:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            decoded = (cursor_codec.decode(cursor) if cursor_codec else
                       json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True).decode()))
            if set(decoded) != {"PK", "SK"} or decoded["PK"] != partition or not isinstance(decoded["SK"], str) or len(decoded["SK"]) > 512:
                raise ValueError()
            kwargs["ExclusiveStartKey"] = decoded
        except (ValueError, UnicodeError, TypeError):
            raise api.APIError(400, "invalid_query", "cursor is invalid for this query.") from None
    result = api.get_table().query(**kwargs)
    items = []
    for ref in result.get("Items", []):
        item = get(api, ref["ref_kind"], ref["ref_id"], required=False)
        if item is None:
            raise api.APIError(503, "storage_inconsistent", "A workflow history reference could not be read.")
        data = item["data"]
        if any(query.get(field) is not None and data.get(field) != query[field]
               for field in ("device_id", "exhibit_id", "status", "type", "scan_type")):
            continue
        value = public(item)
        value = project(value) if project else value
        if value is not None:
            items.append(value)
    last = result.get("LastEvaluatedKey")
    next_cursor = ((cursor_codec.encode(last) if cursor_codec else
                    base64.urlsafe_b64encode(json.dumps(last, separators=(",", ":")).encode()).decode())
                   if last else None)
    return {"items": items, "next_cursor": next_cursor, "consistency": "strong", "page_limit": int(raw_limit)}
