"""Completed interval ingestion and explicit-coverage analytics for PDF items 9/10.

DynamoDB timeline copies live in the base table, so a report never relies on an
asynchronous GSI. Canonical records and their copies are committed atomically.
Coverage is a producer declaration of complete uploads, never a heartbeat guess.
"""

from bisect import bisect_left, bisect_right
import csv
import io
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import auth

MAX_SPAN_DAYS = 31
MAX_BUCKETS = 2048
MAX_SOURCE_RECORDS = 10_000
MAX_RESPONSE_BYTES = 4_000_000
# Lambda synchronously serializes the proxy response itself. The string body
# can expand again when escaped, so its complete envelope needs its own cap.
MAX_PROXY_RESPONSE_BYTES = 5_500_000
STREAMS = ("proximity", "display", "warnings")
COLLECTIONS = {
    "events": ("event", "event_id"),
    "proximity-events": ("proximity_event", "proximity_event_id"),
    "coverage": ("coverage", "coverage_id"),
    "display-history": ("display", "display_id"),
    "warnings": ("warning", "warning_id"),
}
LIMITATIONS = [
    "Proximity records describe detection-zone activity, not unique visitors, viewing attention or per-person dwell time. Groups and stationary objects may trigger one event.",
    "Only completed, producer-reported intervals are included. Point readings do not establish event duration, complete uploads or sustained environmental warnings.",
    "Coverage certifies that all records for the named stream and interval were uploaded. Incomplete coverage makes recorded counts and durations lower bounds; absent coverage is not confirmed quiet.",
    "Warnings are supplied by the producer after its configured persistence logic. This API does not infer or verify that hardware logic.",
    "Sources marked demo are synthetic test data and do not prove hardware operation or human participation.",
    "The shared bearer token is a demonstration credential, not individual staff accounts or role-based access control.",
]


def parse_time(api, value, field):
    if not isinstance(value, str) or not api.UTC_TIMESTAMP.fullmatch(value):
        raise api.APIError(400, "invalid_payload", field + " must be an RFC3339 UTC timestamp ending in Z.")
    try:
        normalized = value[:-1]
        if "." in normalized:
            date, fraction = normalized.split(".")
            normalized = date + "." + fraction.ljust(6, "0")
        return datetime.fromisoformat(normalized + "+00:00")
    except ValueError:
        raise api.APIError(400, "invalid_payload", field + " is not a valid date and time.")


def validate_window(api, start, end, *, report=False):
    if start >= end:
        raise api.APIError(400, "invalid_payload", "start must precede end; intervals are half-open [start,end).")
    if end - start > timedelta(days=MAX_SPAN_DAYS):
        raise api.APIError(400, "invalid_payload", "An interval or report must span at most 31 days.")
    if not report and end > api.now_utc() + timedelta(minutes=5):
        raise api.APIError(400, "invalid_payload", "end cannot be more than five minutes in the future.")


def validate_record(api, entity, data):
    fields = {"event": ("event_id", "name"), "proximity_event": ("proximity_event_id",),
              "coverage": ("coverage_id", "streams"), "display": ("display_id", "exhibit_id"),
              "warning": ("warning_id", "exhibit_id", "warning_type", "message")}[entity]
    api.exact_fields(data, (*fields, "device_id", "start", "end", "source"))
    result = {key: api.identifier(data[key], key) for key in fields if key.endswith("_id")}
    result["device_id"] = api.identifier(data["device_id"], "device_id")
    start, end = (parse_time(api, data[key], key) for key in ("start", "end"))
    validate_window(api, start, end)
    result.update(start=api.timestamp(start), end=api.timestamp(end))
    if data["source"] not in ("demo", "device"):
        raise api.APIError(400, "invalid_payload", "source must be demo or device.")
    result["source"] = data["source"]
    if entity == "event":
        result["name"] = api.text(data["name"], "name", 120)
    if entity == "coverage":
        values = data["streams"]
        if (not isinstance(values, list) or not values or len(values) > len(STREAMS)
                or any(not isinstance(value, str) or value not in STREAMS for value in values)
                or len(set(values)) != len(values)):
            raise api.APIError(400, "invalid_payload", "streams must be a nonempty unique array containing proximity, display and/or warnings.")
        result["streams"] = sorted(values)
    if entity == "warning":
        if data["warning_type"] not in ("temperature", "brightness"):
            raise api.APIError(400, "invalid_payload", "warning_type must be temperature or brightness.")
        result["warning_type"] = data["warning_type"]
        result["message"] = api.text(data["message"], "message", 1000)
    return result


def create_record(api, entity, data, collection):
    record_id = data[entity + "_id"]
    if entity in ("display", "warning"):
        api.read_record("exhibit", data["exhibit_id"])
    item = {**api.record_key(entity, record_id), "entity_type": entity, "data": data,
            "received_at": api.timestamp(api.now_utc())}
    if entity == "event":
        # Events are selected by immutable ID and need no device-timeline copy.
        try:
            api.get_table().put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            return duplicate_response(api, entity, record_id, data)
    else:
        timeline = {**item, "PK": "DEVICE#" + data["device_id"],
                    "SK": entity.upper() + "#" + data["start"] + "#" + record_id}
        table = api.get_table()
        try:
            # The boto3 DynamoDB resource client's transformation handlers
            # serialize these native Python maps, including nested data fields.
            table.meta.client.transact_write_items(TransactItems=[
                {"Put": {"TableName": table.name, "Item": row,
                         "ConditionExpression": "attribute_not_exists(PK)"}}
                for row in (item, timeline)
            ])
        except Exception as exc:
            error = getattr(exc, "response", {})
            if error.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            reasons = error.get("CancellationReasons", [])
            # Do not turn throttling, transaction conflicts or other failures
            # into successful duplicates. Conditional failure means retry read.
            if not any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons):
                raise
            return duplicate_response(api, entity, record_id, data)
    return api.response(201, {"status": "created", "item": api.public_record(item)},
                        {"location": "/api/" + collection + "/" + record_id})


def duplicate_response(api, entity, record_id, data):
    existing = api.read_record(entity, record_id)
    if existing["data"] != data:
        raise api.APIError(409, "id_conflict", "This identifier already belongs to a different payload; stored data was not changed.")
    return api.response(200, {"status": "duplicate", "item": api.public_record(existing)})


def read_timeline(api, device_id, start=None, end=None):
    """Read bounded complete base-table pages, including arbitrary carry-in.

    This small project deliberately reads the device's complete interval history
    to avoid dropping intervals beginning before the selected reporting window.
    It refuses a result above the cap rather than silently truncating a report.
    """
    producer = (auth.principal.get() or {}).get("kind") == "producer"
    if producer and not auth.demo_device(device_id):
        raise api.APIError(403, "forbidden", "This credential is restricted to configured demo devices.")
    items, cursor, seen = [], None, 0
    while True:
        request = {"KeyConditionExpression": "PK = :device",
                   "ExpressionAttributeValues": {":device": "DEVICE#" + device_id},
                   "ConsistentRead": True, "Limit": min(250, MAX_SOURCE_RECORDS + 1 - seen)}
        if cursor:
            request["ExclusiveStartKey"] = cursor
        result = api.get_table().query(**request)
        page = result.get("Items", [])
        seen += len(page)
        items.extend(item for item in page if not producer or (
            item.get("data", {}).get("source") == "demo" and auth.demo_device(item.get("data", {}).get("device_id", ""))))
        if seen > MAX_SOURCE_RECORDS:
            raise api.APIError(413, "report_too_large", "Device history exceeds 10000 interval records. Narrowing dates cannot reduce this prototype's full-history query; archive or partition history first.")
        following = result.get("LastEvaluatedKey")
        if not following:
            if start is not None and end is not None and start < end:
                items.extend(read_sensor_samples(api, device_id, start, end, producer))
            return items
        if following == cursor:
            raise api.APIError(503, "service_unavailable", "Storage pagination did not advance.")
        cursor = following


def read_sensor_samples(api, device_id, start, end, producer=False):
    """Read existing point observations; never infer intervals or coverage.

    The readings GSI is eventual. Query whole boundary seconds because legacy
    recorded_at values can use different fractional-second precision, then
    apply the exact half-open window in calculate().
    """
    items, cursor, seen = [], None, 0
    while True:
        request = {"IndexName": "device-readings-index",
                   "KeyConditionExpression": "GSI1PK = :device AND GSI1SK BETWEEN :start AND :end",
                   "ExpressionAttributeValues": {":device": "DEVICE#" + device_id,
                       ":start": start.strftime("%Y-%m-%dT%H:%M:%S"),
                       ":end": end.strftime("%Y-%m-%dT%H:%M:%S") + "~"},
                   "ScanIndexForward": True, "Limit": min(250, MAX_SOURCE_RECORDS + 1 - seen)}
        if cursor:
            request["ExclusiveStartKey"] = cursor
        result = api.get_table().query(**request)
        page = result.get("Items", [])
        seen += len(page)
        if seen > MAX_SOURCE_RECORDS:
            raise api.APIError(413, "report_too_large", "Selected period exceeds 10000 sensor readings; narrow the date range.")
        items.extend(item for item in page if not producer or (
            item.get("data", {}).get("source") == "demo" and auth.demo_device(item.get("data", {}).get("device_id", ""))))
        following = result.get("LastEvaluatedKey")
        if not following:
            return items
        if following == cursor:
            raise api.APIError(503, "service_unavailable", "Storage pagination did not advance.")
        cursor = following


def select_window(api, query):
    event_id = query.get("event_id")
    if event_id is not None:
        if any(key in query for key in ("device_id", "start", "end")):
            raise api.APIError(400, "invalid_query", "Use event_id OR device_id,start,end, without mixing selectors.")
        event = api.public_record(api.read_record("event", api.identifier(event_id, "event_id")))
        if (auth.principal.get() or {}).get("kind") == "producer" and (
                event.get("source") != "demo" or not auth.demo_device(event.get("device_id", ""))):
            raise api.APIError(403, "forbidden", "This credential cannot access that event.")
        device_id, start_value, end_value = (event[key] for key in ("device_id", "start", "end"))
    else:
        event = None
        device_id = api.identifier(query.get("device_id"), "device_id")
        start_value, end_value = query.get("start"), query.get("end")
    start, end = parse_time(api, start_value, "start"), parse_time(api, end_value, "end")
    if (auth.principal.get() or {}).get("kind") == "producer" and not auth.demo_device(device_id):
        raise api.APIError(403, "forbidden", "This credential is restricted to configured demo devices.")
    validate_window(api, start, end, report=True)
    return device_id, start, end, event


def configuration(api, query):
    allowed = {"event_id", "device_id", "start", "end", "interval_minutes", "timezone", "format", "exhibit_id"}
    if set(query) - allowed:
        raise api.APIError(400, "invalid_query", "Unsupported query parameter.")
    raw_interval = query.get("interval_minutes", "15")
    if not isinstance(raw_interval, str) or not api.re.fullmatch(r"[0-9]{1,4}", raw_interval) or not 1 <= int(raw_interval) <= 1440:
        raise api.APIError(400, "invalid_query", "interval_minutes must be an integer between 1 and 1440.")
    zone_name = query.get("timezone", "Asia/Hong_Kong")
    if not isinstance(zone_name, str) or len(zone_name) > 100:
        raise api.APIError(400, "invalid_query", "timezone must name an IANA time zone.")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise api.APIError(400, "invalid_query", "timezone must name an available IANA time zone.")
    output_format = query.get("format", "json")
    if output_format not in ("json", "csv"):
        raise api.APIError(400, "invalid_query", "format must be json or csv.")
    device, start, end, event = select_window(api, query)
    if "exhibit_id" in query:
        api.identifier(query["exhibit_id"], "exhibit_id")
    interval = timedelta(minutes=int(raw_interval))
    if (end - start + interval - timedelta(microseconds=1)) // interval > MAX_BUCKETS:
        raise api.APIError(400, "invalid_query", "The selected interval produces more than 2048 buckets; increase interval_minutes.")
    return device, start, end, event, interval, zone_name, zone, output_format


def clip(interval, start, end):
    left, right = max(interval[0], start), min(interval[1], end)
    return (left, right) if left < right else None


def union(intervals):
    result = []
    for left, right in sorted(intervals):
        if result and left <= result[-1][1]:
            result[-1] = (result[-1][0], max(right, result[-1][1]))
        else:
            result.append((left, right))
    return result


def seconds(intervals):
    return sum((right - left) // timedelta(microseconds=1) for left, right in intervals) / 1_000_000


def interval_measure(intervals):
    """Return O(log N) exact-microsecond duration queries over sorted unions."""
    starts = [left for left, _right in intervals]
    ends = [right for _left, right in intervals]
    prefix = [0]
    microsecond = timedelta(microseconds=1)
    for left, right in intervals:
        prefix.append(prefix[-1] + (right - left) // microsecond)

    def integral(point):
        index = bisect_right(ends, point)
        value = prefix[index]
        if index < len(starts) and point > starts[index]:
            value += (point - starts[index]) // microsecond
        return value

    def measure(left, right):
        return integral(right) - integral(left)

    return measure


def gaps(covered, start, end):
    result, cursor = [], start
    for left, right in union(covered):
        if cursor < left:
            result.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        result.append((cursor, end))
    return result


def interval_json(api, interval):
    return {"start": api.timestamp(interval[0]), "end": api.timestamp(interval[1]),
            "seconds": seconds([interval])}


def intersection(first, second):
    """Intersect two sorted, disjoint interval lists without double counting."""
    result, i, j = [], 0, 0
    while i < len(first) and j < len(second):
        part = clip(first[i], *second[j])
        if part:
            result.append(part)
        if first[i][1] <= second[j][1]:
            i += 1
        else:
            j += 1
    return result


def subtraction(scope, covered):
    """Intervals in scope that are not covered; both inputs are unions."""
    result, index = [], 0
    for left, right in scope:
        while index < len(covered) and covered[index][1] <= left:
            index += 1
        cursor, following = left, index
        while following < len(covered) and covered[following][0] < right:
            start, end = covered[following]
            if cursor < start:
                result.append((cursor, min(start, right)))
            cursor = max(cursor, end)
            if cursor >= right:
                break
            following += 1
        if cursor < right:
            result.append((cursor, right))
    return result


def display_assignment(records, start, end, exhibit_id):
    """A sweep treats same-exhibit overlap as known and conflicting IDs as unknown.

    The current management assignment is deliberately never consulted here.
    """
    changes = {start: [], end: []}
    for data, _original, (left, right) in records:
        changes.setdefault(left, []).append((data["exhibit_id"], 1))
        changes.setdefault(right, []).append((data["exhibit_id"], -1))
    spans = {name: [] for name in ("selected", "other", "unknown", "ambiguous")}
    points, active = sorted(changes), {}
    for index, left in enumerate(points[:-1]):
        for identity, delta in changes[left]:
            active[identity] = active.get(identity, 0) + delta
            if active[identity] == 0:
                del active[identity]
        right = points[index + 1]
        if not active:
            kind = "unknown"
        elif len(active) > 1:
            kind = "ambiguous"
        else:
            kind = "selected" if exhibit_id in active else "other"
        if left < right:
            spans[kind].append((left, right))
    return {key: union(value) for key, value in spans.items()}


def calculate(api, items, device, start, end, event, interval, zone_name, zone,
              exhibit_id=None, captured_at=None):
    # Capture once before reading timeline pages in callers. Future intervals
    # remain in the requested grid, but never contribute missing or quiet time.
    captured_at = captured_at or api.now_utc()
    effective_end = min(end, max(start, captured_at))
    evaluated = [(start, effective_end)] if start < effective_end else []
    records = {entity: [] for entity in ("proximity_event", "coverage", "display", "warning")}
    samples = {}
    for item in items:
        entity, data = item["entity_type"], api.public_record(item)
        if entity == "reading" and data.get("device_id") == device:
            at = parse_time(api, data["recorded_at"], "recorded_at")
            if start <= at < effective_end and (not exhibit_id or data.get("exhibit_id") == exhibit_id):
                samples[data["reading_id"]] = data
        if entity in records:
            original = (parse_time(api, data["start"], "start"), parse_time(api, data["end"], "end"))
            clipped = clip(original, start, effective_end)
            if clipped:
                records[entity].append((data, original, clipped))
    for values in records.values():
        values.sort(key=lambda row: (row[1][0], row[0].get("received_at", ""), json.dumps(row[0], default=api.json_default, sort_keys=True)))
    samples = sorted(samples.values(), key=lambda row: (parse_time(api, row["recorded_at"], "recorded_at"), row["reading_id"]))
    sample_times = [parse_time(api, row["recorded_at"], "recorded_at") for row in samples]
    occupied_sample_times = [at for at, row in zip(sample_times, samples) if row["occupied"]]
    raw_occupancy = union([row[2] for row in records["proximity_event"]])
    raw_coverage = {stream: union([row[2] for row in records["coverage"] if stream in row[0]["streams"]]) for stream in STREAMS}
    assignment = display_assignment(records["display"], start, effective_end, exhibit_id) if evaluated else {
        key: [] for key in ("selected", "other", "unknown", "ambiguous")}
    if exhibit_id is None:
        scope, selected = evaluated, evaluated
        coverage, occupancy = raw_coverage, raw_occupancy
    else:
        selected = assignment["selected"]
        # Known-other time is out of scope. Unknown/contradictory assignment
        # remains in the denominator and cannot be certified for this exhibit.
        scope = union(selected + assignment["unknown"] + assignment["ambiguous"])
        assignment_covered = intersection(selected, raw_coverage["display"])
        coverage = {stream: intersection(raw_coverage[stream], assignment_covered) for stream in STREAMS}
        occupancy = intersection(raw_occupancy, selected)
    missing = {stream: subtraction(scope, values) for stream, values in coverage.items()}
    selected_starts = [left for left, _right in selected]
    selected_ends = [right for _left, right in selected]
    uncertain = union(assignment["unknown"] + assignment["ambiguous"])
    uncertain_starts = [left for left, _right in uncertain]
    uncertain_ends = [right for _left, right in uncertain]

    def attributed(point):
        index = bisect_right(selected_starts, point) - 1
        return index >= 0 and point < selected_ends[index]

    def unattributed(point):
        index = bisect_right(uncertain_starts, point) - 1
        return index >= 0 and point < uncertain_ends[index]

    event_starts = [row[1][0] for row in records["proximity_event"] if attributed(row[1][0])]
    occupied_measure = interval_measure(occupancy)
    coverage_measure = interval_measure(coverage["proximity"])
    scope_measure = interval_measure(scope)
    assignment_measures = {key: interval_measure(value) for key, value in assignment.items()}

    def scope_fields(left, right):
        return {"scope_seconds": scope_measure(left, right) / 1_000_000,
                "known_display_seconds": assignment_measures["selected"](left, right) / 1_000_000 if exhibit_id else None,
                "unknown_assignment_seconds": assignment_measures["unknown"](left, right) / 1_000_000 if exhibit_id else 0,
                "ambiguous_assignment_seconds": assignment_measures["ambiguous"](left, right) / 1_000_000 if exhibit_id else 0,
                "outside_scope_seconds": assignment_measures["other"](left, right) / 1_000_000 if exhibit_id else 0}

    buckets, left = [], start
    while left < end:
        right = min(left + interval, end)
        evaluated_right = min(right, max(left, effective_end))
        width = (evaluated_right - left).total_seconds()
        fields = scope_fields(left, evaluated_right)
        covered_microseconds = coverage_measure(left, evaluated_right)
        covered = covered_microseconds / 1_000_000
        occupied = occupied_measure(left, evaluated_right) / 1_000_000
        event_count = bisect_left(event_starts, evaluated_right) - bisect_left(event_starts, left)
        has_observation = occupied > 0 or event_count > 0
        denominator_microseconds = scope_measure(left, evaluated_right)
        complete = covered_microseconds == denominator_microseconds if denominator_microseconds else None
        known = bool(covered or has_observation)
        if not width:
            status = "future"
        elif exhibit_id and not fields["known_display_seconds"]:
            status = ("ambiguous_assignment" if fields["ambiguous_assignment_seconds"] else
                      "unknown_assignment" if fields["unknown_assignment_seconds"] else "no_known_display")
        else:
            status = ("recorded_activity" if has_observation else "confirmed_quiet") if complete else ("partial" if covered or has_observation else "missing")
        buckets.append({"start": api.timestamp(left), "end": api.timestamp(right),
                        "local_start": left.astimezone(zone).isoformat(), "local_end": right.astimezone(zone).isoformat(),
                        "evaluated_end": api.timestamp(evaluated_right) if width else None,
                        "evaluated_seconds": width, "future_seconds": (right - evaluated_right).total_seconds(),
                        "status": status, "coverage_seconds": covered,
                        "sensor_sample_count": bisect_left(sample_times, evaluated_right) - bisect_left(sample_times, left),
                        "occupied_sample_count": bisect_left(occupied_sample_times, evaluated_right) - bisect_left(occupied_sample_times, left),
                        "coverage_fraction": round(covered_microseconds / denominator_microseconds, 6) if denominator_microseconds else None,
                        "coverage_complete": complete, **fields,
                        "proximity_event_count": event_count if known else None,
                        "recorded_occupied_seconds": occupied if known else None})
        left = right
    span, scope_seconds = seconds(evaluated), seconds(scope)
    any_known = bool(coverage["proximity"] or occupancy)
    interpretation = ("future_only" if not evaluated else "no_known_display" if not scope else
                      "complete_recorded_intervals" if not missing["proximity"] else "recorded_lower_bounds")
    # Split details using indexed scope ranges. Bound the expansion as well as
    # the original history to avoid pathological alternating-display payloads.
    details, fragment_count = {}, 0
    for entity, output in (("proximity_event", "proximity_events"), ("display", "display_history"),
                           ("warning", "environmental_warnings"), ("coverage", "coverage_records")):
        rows = []
        ranges = scope if entity == "coverage" else selected
        range_ends = [right for _left, right in ranges]
        for row in records[entity]:
            if exhibit_id and entity in ("display", "warning") and row[0]["exhibit_id"] != exhibit_id:
                continue
            index = bisect_right(range_ends, row[2][0])
            while index < len(ranges) and ranges[index][0] < row[2][1]:
                part = clip(row[2], *ranges[index])
                if part:
                    detail = {**row[0], "clipped_start": api.timestamp(part[0]), "clipped_end": api.timestamp(part[1]),
                              "clipped_duration_seconds": seconds([part])}
                    if entity == "proximity_event" and exhibit_id:
                        detail["event_start_attributed"] = part[0] <= row[1][0] < part[1]
                    rows.append(detail)
                    fragment_count += 1
                    if fragment_count > MAX_SOURCE_RECORDS:
                        raise api.APIError(413, "report_too_large", "Attribution expands to more than 10000 detail fragments; narrow the selected date range.")
                index += 1
        details[output] = rows
    details["sensor_samples"] = samples
    summary = {"proximity_event_count": len(event_starts) if any_known else None,
               "sensor_sample_count": len(samples), "occupied_sample_count": len(occupied_sample_times),
               "recorded_occupied_seconds": seconds(occupancy) if any_known else None,
               "coverage_seconds": seconds(coverage["proximity"]), "missing_seconds": seconds(missing["proximity"]),
               "coverage_complete": not missing["proximity"] if scope else None,
               "totals_interpretation": interpretation, **scope_fields(start, effective_end),
               "warning_count": len({row["warning_id"] for row in details["environmental_warnings"]}),
               "display_interval_count": len({row["display_id"] for row in details["display_history"]}),
               "warning_count_interpretation": "complete_recorded_intervals" if scope and not missing["warnings"] else "recorded_lower_bounds",
               "display_history_interpretation": "complete_recorded_intervals" if scope and not missing["display"] else "recorded_lower_bounds"}
    sources = sorted({row[0]["source"] for values in records.values() for row in values}
                     | {row["source"] for row in samples} | ({event["source"]} if event else set()))
    denominator_note = ("Known selected-display time plus unknown and contradictory assignment time; known-other time and future time are excluded. Stream coverage must also certify display uploads within unambiguous selected spans."
                        if exhibit_id else "Evaluated device-wide elapsed time; future time is excluded.")
    base = {"device_id": device, "start": api.timestamp(start), "end": api.timestamp(end),
            "event": event, "interval_minutes": int(interval.total_seconds() // 60), "timezone": zone_name,
            "filter": {"exhibit_id": exhibit_id, "attribution": "unambiguous_display_intervals" if exhibit_id else "device_wide"},
            "evaluation": {"captured_at": api.timestamp(captured_at), "requested_start": api.timestamp(start), "requested_end": api.timestamp(end),
                           "effective_start": api.timestamp(start) if evaluated else None,
                           "effective_end": api.timestamp(effective_end) if evaluated else None,
                           "evaluated_seconds": span, "future_seconds": (end - effective_end).total_seconds(),
                           "status": "future_only" if not evaluated else "cutoff_at_capture" if effective_end < end else "complete_window"},
            "bucket_alignment": "fixed elapsed UTC intervals beginning at requested start; final bucket may be shorter; metrics stop at evaluated_end",
            "interval_semantics": "half-open [start,end); event counts count original starts only; occupied duration merges overlapping events and is split at unambiguous exhibit changes",
            "consistency": "strong", "snapshot_semantics": "Each timeline page is strongly consistent; concurrent uploads can change a multi-page report. Re-run after uploads finish.",
            "sensor_sample_semantics": {"consistency": "eventual", "attribution": "reading_exhibit_id",
                "note": "Sensor samples are point observations from the readings index, which may lag recent uploads. Occupied samples are not event starts, visitors, occupied duration or completeness coverage. Exhibit filtering uses the exhibit ID recorded by the producer, not confirmed display history."},
            "summary": summary, "buckets": buckets,
            "data_completeness": {stream: {"coverage_seconds": seconds(coverage[stream]), "missing_seconds": seconds(missing[stream]),
                                                "denominator_seconds": scope_seconds, "denominator_note": denominator_note,
                                                "coverage_fraction": round(seconds(coverage[stream]) / scope_seconds, 6) if scope_seconds else None,
                                                "complete": not missing[stream] if scope else None} for stream in STREAMS},
            "missing_data": {stream: [interval_json(api, part) for part in missing[stream]] for stream in STREAMS},
            "provenance": {"sources": sources, "contains_demo_data": "demo" in sources},
            "data_quality": {"overlapping_display_intervals": seconds([row[2] for row in records["display"]]) > seconds(union([row[2] for row in records["display"]])),
                             "unknown_display_intervals": [interval_json(api, part) for part in assignment["unknown"]],
                             "contradictory_display_intervals": [interval_json(api, part) for part in assignment["ambiguous"]],
                             "unattributed_proximity_event_count": sum(unattributed(row[1][0]) for row in records["proximity_event"]) if exhibit_id else 0,
                             "unattributed_recorded_occupied_seconds": seconds(intersection(raw_occupancy, uncertain)) if exhibit_id else 0,
                             "display_history_note": "Display intervals are producer-reported history, not physical verification. Same-exhibit overlaps are unioned; different-exhibit overlaps are contradictory and unattributed. Current profiles never establish historical assignment. Filtered detail counts refer to distinct source IDs; durations may be split into several rows."},
            "limitations": LIMITATIONS}
    return base, details


def csv_response(api, result, details):
    """One downloadable report with typed rows; never spreadsheet formulas."""
    output = io.StringIO(newline="")
    fields = ("section", "device_id", "start", "end", "record_id", "status", "proximity_event_count",
              "recorded_occupied_seconds", "coverage_seconds", "source", "detail")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()

    def emit(**row):
        # Device IDs are restricted, but free-text names/messages may begin with
        # formula prefixes. Prefix such cells to keep exported text inert.
        writer.writerow({key: ("'" + value if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))) else value)
                         for key, value in row.items()})

    emit(section="summary", device_id=result["device_id"], start=result["start"], end=result["end"],
         proximity_event_count=result["summary"]["proximity_event_count"],
         recorded_occupied_seconds=result["summary"]["recorded_occupied_seconds"],
         coverage_seconds=result["summary"]["coverage_seconds"],
         detail=json.dumps({"summary": result["summary"], "provenance": result["provenance"],
                            "event": result["event"], "timezone": result["timezone"],
                            "filter": result.get("filter"), "evaluation": result.get("evaluation"),
                            "date_selection": result.get("date_selection"),
                            "data_completeness": result.get("data_completeness", {}),
                            "data_quality": result.get("data_quality", {}),
                            "consistency": result.get("consistency"),
                            "snapshot_semantics": result.get("snapshot_semantics")}, default=api.json_default))
    for bucket in result["buckets"]:
        emit(section="heatmap", device_id=result["device_id"], **{key: bucket[key] for key in
             ("start", "end", "status", "proximity_event_count", "recorded_occupied_seconds", "coverage_seconds")},
             detail=json.dumps({key: value for key, value in bucket.items() if key not in (
                 "start", "end", "status", "proximity_event_count", "recorded_occupied_seconds", "coverage_seconds")}))
    for section, rows in details.items():
        for row in rows:
            if section == "sensor_samples":
                emit(section=section, device_id=result["device_id"], start=row["recorded_at"],
                     record_id=row["reading_id"], status="occupied" if row["occupied"] else "clear",
                     source=row["source"], detail=json.dumps(row, default=api.json_default))
                continue
            record_id = next((row[key] for key in ("proximity_event_id", "display_id", "warning_id", "coverage_id") if key in row), "")
            emit(section=section, device_id=result["device_id"], start=row["clipped_start"], end=row["clipped_end"],
                 record_id=record_id, source=row["source"], detail=json.dumps(row, default=api.json_default))
    for stream, intervals in result["missing_data"].items():
        for row in intervals:
            emit(section="missing_data_" + stream, device_id=result["device_id"], start=row["start"], end=row["end"], detail=str(row["seconds"]))
    for value in result["limitations"]:
        emit(section="limitation", detail=value)
    body = output.getvalue()
    result = {"statusCode": 200, "headers": {**api.CORS_HEADERS, "content-type": "text/csv; charset=utf-8",
            "content-disposition": 'attachment; filename="reshow-event-report.csv"', "cache-control": "no-store",
            "x-content-type-options": "nosniff"}, "body": body}
    return bounded_proxy_response(api, result)


def bounded_proxy_response(api, result):
    body_bytes = len(result["body"].encode("utf-8"))
    envelope_bytes = len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    if body_bytes > MAX_RESPONSE_BYTES or envelope_bytes > MAX_PROXY_RESPONSE_BYTES:
        raise api.APIError(413, "report_too_large", "Report exceeds the safe response size (4 MB body or 5.5 MB serialized Lambda envelope); narrow the selected date range.")
    return result


def bounded_response(api, payload):
    return bounded_proxy_response(api, api.response(200, payload))


def route(api, method, path, query, event, headers):
    if path in ("/api/analytics/heatmap", "/api/reports/event"):
        if method != "GET":
            raise api.APIError(405, "method_not_allowed", "This route accepts GET.")
        device, start, end, selected, interval, zone_name, zone, output_format = configuration(api, query)
        captured = api.now_utc()
        items = read_timeline(api, device, start, min(end, captured)) if start < captured else []
        base, details = calculate(api, items, device, start, end, selected, interval, zone_name, zone,
                                  exhibit_id=query.get("exhibit_id"), captured_at=captured)
        is_report = path == "/api/reports/event"
        if output_format == "csv":
            return csv_response(api, base, details if is_report else {})
        return bounded_response(api, {**base, **(details if is_report else {})})
    for collection, (entity, field) in COLLECTIONS.items():
        base = "/api/" + collection
        if path == base:
            if method != "POST":
                raise api.APIError(405, "method_not_allowed", "This route accepts POST; use the exact-ID GET route to read a record.")
            if query:
                raise api.APIError(400, "invalid_query", "POST routes do not accept query parameters.")
            return create_record(api, entity, validate_record(api, entity, api.parse_body(event, headers)), collection)
        if path.startswith(base + "/"):
            if method != "GET":
                raise api.APIError(405, "method_not_allowed", "This route accepts GET.")
            if query:
                raise api.APIError(400, "invalid_query", "Exact GET routes do not accept query parameters.")
            record_id = api.identifier(path[len(base) + 1:], field)
            return api.response(200, {"item": api.public_record(api.read_record(entity, record_id)), "consistency": "strong"})
    return None
