"""RFID edit sessions and atomic publication of the assigned showcase label."""
import re
import uuid

import workflow_store as store
import workflows


def session_view(api, case, session):
    current = session["data"] if session else {}
    command_id = current.get("command_id")
    command = store.get(api, "command", command_id)["data"] if command_id else None
    return {
        "showcase_id": case["SK"],
        "exhibit_id": case["data"]["confirmed_exhibit_id"],
        "phase": "pending_edit" if current.get("edit_id") else "idle",
        "edit_id": current.get("edit_id"),
        "last_tap": current.get("last_tap"),
        "delivery": {key: command.get(key) for key in ("status", "failure_reason", "execute_before")}
        if command else None,
    }


def record_tap(api, data):
    workflows.fields(api, data, ("event_id", "device_id", "recorded_at", "source", "result"), ("role",))
    event_id = api.identifier(data["event_id"], "event_id")
    device_id = api.identifier(data["device_id"], "device_id")
    outcome = workflows.enum(api, data["result"], "result", ("accepted", "denied"))
    role = workflows.enum(api, data.get("role"), "role", ("door", "epaper", "edit"), nullable=True)
    if (outcome == "accepted") != (role is not None):
        raise api.APIError(400, "invalid_payload", "Supply a role for accepted taps only.")
    clean = {"event_id": event_id, "device_id": device_id, "role": role, "result": outcome,
             "recorded_at": workflows.utc(api, data["recorded_at"]), "source": "device"}
    existing = store.get(api, "rfid-event", event_id, required=False)
    if existing:
        if existing["data"] != clean:
            raise api.APIError(409, "id_conflict", "This event ID already belongs to another tap.")
        return workflows.result(api, existing, False)
    case = workflows.bound_showcase(api, device_id)
    old = store.get(api, "rfid-session", case["SK"], required=False)
    state = dict(old["data"]) if old else {}
    # Late deliveries are retained as events but cannot reopen or cancel a newer session.
    last = state.get("last_tap")
    if last and clean["recorded_at"] < last["recorded_at"]:
        item, created = store.create(api, "rfid-event", event_id, clean)
        return workflows.result(api, item, created)
    if role in ("door", "epaper") and not state.get("edit_id"):
        state["edit_id"] = "rfid-" + uuid.uuid4().hex
    elif role == "edit":
        state["edit_id"] = None
    state["last_tap"] = {key: clean[key] for key in ("role", "result", "recorded_at")}
    session = store.make(api, "rfid-session", case["SK"], state, old)
    event = store.make(api, "rfid-event", event_id, clean)
    store.transact(api, [store.put_action(api, session, old), store.put_action(api, event)])
    return workflows.result(api, event, True)


def finish(api, case, old, data, publish):
    workflows.fields(api, data, ("edit_id", "exhibit_id", "description") if publish else ("edit_id",))
    edit_id = api.identifier(data["edit_id"], "edit_id")
    if publish:
        exhibit_id = api.identifier(data["exhibit_id"], "exhibit_id")
        description = data["description"]
        if not isinstance(description, str) or not description.strip() or not re.fullmatch(r"[ -~]{1,180}", description):
            raise api.APIError(400, "invalid_payload", "Use 1–180 printable ASCII characters.")
        previous = store.get(api, "command", edit_id, required=False)
        if previous:
            command = previous["data"]
            if (command["showcase_id"] == case["SK"] and command["type"] == "label"
                    and command["exhibit_id"] == exhibit_id
                    and command["payload"]["content"]["description"] == description
                    and old and old["data"].get("command_id") == edit_id):
                return api.response(200, {"data": session_view(api, case, old)})
            raise api.APIError(409, "stale_session", "This edit was already published with different content or superseded.")
    if not old or old["data"].get("edit_id") != edit_id:
        raise api.APIError(409, "stale_session", "The RFID session changed. Refresh and try again.")
    state = {**old["data"], "edit_id": None}
    if publish:
        if exhibit_id != case["data"]["confirmed_exhibit_id"]:
            raise api.APIError(409, "assignment_required", "Select the confirmed exhibit for this showcase.")
        state["command_id"] = edit_id
    session = store.make(api, "rfid-session", case["SK"], state, old)
    action = store.put_action(api, session, old)
    if publish:
        existing = store.get(api, "label-draft", exhibit_id, required=False)
        profile = store.get(api, "exhibit-profile", exhibit_id)["data"]
        content = dict(existing["data"]["content"]) if existing else {
            "title": profile["name"][:100], "collection": profile.get("collection", ""), "image_media_id": profile.get("image_media_id"),
        }
        content["description"] = description
        draft, actions = workflows.prepare_draft(api, exhibit_id, content, existing["version"] if existing else 0)
        person = api.auth.principal.get()
        workflows.create_command(api, {
            "command_id": edit_id, "showcase_id": case["SK"], "type": "label",
            "payload": {"exhibit_id": exhibit_id, "draft_version": draft["version"]},
            "actor": person["staff_id"] + ":" + person["subject"],
        }, prepared_draft=draft, additional_actions=[action, *actions])
    else:
        store.transact(api, [action])
    return api.response(200, {"data": session_view(api, case, session)})


def route(api, method, path, query, event, headers):
    person = api.auth.principal.get()
    if path == "/api/rfid-events" and method == "POST" and person["kind"] == "hardware":
        workflows.no_query(api, query)
        return record_tap(api, api.parse_body(event, headers))
    match = re.fullmatch(r"/api/rfid-sessions/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(/publish|/cancel)?", path)
    if not match or person["kind"] != "human":
        return None
    workflows.no_query(api, query)
    if method != ("POST" if match[2] else "GET"):
        raise api.APIError(405, "method_not_allowed", "Unsupported RFID session operation.")
    case = store.get(api, "showcase", match[1])
    session = store.get(api, "rfid-session", match[1], required=False)
    if match[2]:
        return finish(api, case, session, api.parse_body(event, headers), match[2] == "/publish")
    return api.response(200, {"data": session_view(api, case, session)})
