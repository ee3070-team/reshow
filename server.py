import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import ssl
import threading
import time
from email import policy
from email.message import Message
from email.parser import BytesParser
from functools import partial
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
MAX_IMAGE_SIZE = 4 * 1024 * 1024
MAX_REQUEST_SIZE = MAX_IMAGE_SIZE + 32 * 1024
MAX_RESPONSE_SIZE = 256 * 1024
MAX_RFID_EVENT_SIZE = 4096
TRIPO_API = "https://openapi.tripo3d.ai/v3"
TRIPO_MODEL = "v3.1-20260211"
EPAPER_DESCRIPTION_FILE = ROOT / ".reshow-epaper-description.json"
RFID_EVENT_LOCK = threading.Lock()
RFID_WORKFLOW = {
    "phase": "idle",
    "uid": None,
    "exhibit_id": None,
    "edit_id": None,
}
RFID_LATEST_DEVICE_EVENT = None
EPAPER_DELIVERED_DESCRIPTION = None
PROMPT = """Analyze the attached image and identify the visible artifact. Return exactly one valid JSON object and no Markdown or extra text, using precisely these two keys:
{
  "name": "A short, specific title for the visible image",
  "description": "A concise description of details visible in the image."
}
Do not return a general image summary instead of this JSON. Do not infer facts that cannot be seen."""
THREEJS_GROUP_PROMPT = """Analyze the attached artifact image and build a recognizable low-poly Three.js model from its silhouette and visible details, not a generic blob. Return exactly one valid JSON object and no Markdown, with this schema:
{"parts":[{"shape":"sphere","position":[0,0,0],"scale":[1,1,1],"color":"#aabbcc"}]}
Include 8 to 12 parts. Each part must have exactly the keys shape, position, scale, and color. shape must be sphere, box, or cylinder. position must contain three numbers from -2.5 to 2.5. scale must contain three numbers greater than 0 and no greater than 3. color must be a six-digit hex color. Use boxes and cylinders for the main body, frame, rim, visor, handle, or base. Use at most three spheres, only for small rounded details or when the artifact itself is spherical; never replace a non-spherical artifact with one large sphere. Make parts different sizes and place them to form a clear silhouette instead of stacking them at the origin. Use a restrained palette of two to four colors based on the image. Keep the model upright with its main axis on Y. Do not include JavaScript, HTML, explanations, or facts not visible in the image."""
# The prompts ask for the full number of parts, but the free model often returns
# fewer (or stops early), so the accepted ranges start at the smallest usable
# count. Anything the model did return is kept instead of falling back to the
# local demo model.
THREEJS_GROUP_STAGES = {
    "silhouette": {
        "previous": (0, 0),
        "new": (1, 4),
        "total": (1, 4),
        "instruction": "Build only the object's main silhouette from 3 to 4 large parts.",
    },
    "structure": {
        "previous": (1, 4),
        "new": (1, 4),
        "total": (2, 8),
        "instruction": "Add 3 to 4 major structural features that make this specific object recognizable.",
    },
    "details": {
        "previous": (2, 8),
        "new": (1, 4),
        "total": (3, 12),
        "instruction": "Add 2 to 4 small, visible details that refine this specific object.",
    },
}


class UploadError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def read_epaper_description():
    stored = json.loads(EPAPER_DESCRIPTION_FILE.read_text(encoding="utf-8"))
    description = stored["description"]
    if not isinstance(description, str) or not re.fullmatch(r"[\x20-\x7e]{1,180}", description):
        raise ValueError("Invalid stored description")
    return description


def decode_json_response(response):
    try:
        value = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # A redirect must never receive a visitor's BYOK Authorization header.
        return None


def read_cloudflare_response(response):
    body = response.read(MAX_RESPONSE_SIZE + 1)
    if len(body) > MAX_RESPONSE_SIZE:
        raise RuntimeError("Cloudflare returned an oversized response.")
    return decode_json_response(body)


def cloudflare_request(endpoint, token, payload, timeout=40):
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=timeout) as response:
            return response.status, read_cloudflare_response(response)
    except HTTPError as error:
        # Upstream bodies may echo credentials. Do not return or log them.
        return error.code, {}
    except OSError as error:
        raise RuntimeError("Cloudflare Workers AI could not complete the request. Try again.") from error


def tripo_request(path, token, body=None, content_type="application/json"):
    request = Request(
        TRIPO_API + path,
        data=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": content_type},
        method="POST" if body is not None else "GET",
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=25) as response:
            raw = response.read(MAX_RESPONSE_SIZE + 1)
            if len(raw) > MAX_RESPONSE_SIZE:
                raise RuntimeError("Tripo returned an oversized response.")
            result = decode_json_response(raw)
    except HTTPError as error:
        raise RuntimeError(
            f"Tripo rejected the request (HTTP {error.code}). Check your API key, balance, and model access."
        ) from error
    except OSError as error:
        raise RuntimeError(
            "Could not reach Tripo. If a generation was submitted, check your Tripo task history before retrying; it may still be charged."
        ) from error
    if result.get("code") != 0 or not isinstance(result.get("data"), dict):
        code = result.get("code")
        suffix = f" (code {code})" if isinstance(code, int) else ""
        raise RuntimeError("Tripo could not complete the request" + suffix + ". Check your balance and task history.")
    return result["data"]


def process_tripo_request(body):
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UploadError(400, "Invalid Tripo request.") from error
    if not isinstance(request, dict):
        raise UploadError(400, "Invalid Tripo request.")
    token = request.get("api_key")
    if not isinstance(token, str) or len(token) > 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/\-]+=*", token):
        raise UploadError(400, "Enter a valid Tripo API key.")
    action = request.get("action")
    if action == "status":
        task_id = request.get("task_id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise UploadError(400, "Invalid Tripo task ID.")
        data = tripo_request("/tasks/" + task_id, token)
        status = data.get("status")
        if not isinstance(status, str):
            raise RuntimeError("Tripo returned an invalid task status.")
        result = {"task_id": task_id, "status": status, "progress": data.get("progress")}
        if isinstance(data.get("type"), str):
            result["type"] = data["type"]
        if status == "success":
            output = data.get("output")
            model_url = output.get("model_url") if isinstance(output, dict) else None
            if not isinstance(model_url, str):
                raise RuntimeError("Tripo completed the task without a model URL.")
            try:
                url = urlsplit(model_url)
                valid_url = url.scheme == "https" and url.hostname and not url.username and not url.password
            except ValueError:
                valid_url = False
            if not valid_url:
                raise RuntimeError("Tripo returned an invalid model URL.")
            result["model_url"] = model_url
            credits = data.get("credits_consumed")
            if isinstance(credits, (int, float)) and not isinstance(credits, bool):
                result["credits_consumed"] = credits
        elif status in ("failed", "cancelled", "banned", "expired"):
            code = data.get("error_code")
            result["error"] = "Tripo task " + status + (f" (code {code})." if isinstance(code, int) else ".")
        return result
    if action not in ("image", "retexture"):
        raise UploadError(400, "Choose a supported Tripo generation mode.")
    if action == "retexture":
        source_task_id = request.get("source_task_id")
        if not isinstance(source_task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source_task_id):
            raise UploadError(400, "Enter a valid source generation task ID.")
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1024:
            raise UploadError(400, "Enter a texture description of up to 1024 characters.")
        path = "/models/texture"
        payload = {
            "input": source_task_id,
            "texture_prompt": {"text": prompt.strip()},
            "texture_quality": "standard",
            "pbr": True,
        }
    else:
        encoded = request.get("image")
        if not isinstance(encoded, str) or len(encoded) > MAX_REQUEST_SIZE:
            raise UploadError(400, "Choose a valid image.")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise UploadError(400, "The image could not be decoded.") from error
        if not image.startswith(b"\xff\xd8\xff") or len(image) > MAX_IMAGE_SIZE:
            raise UploadError(400, "Choose a JPEG image smaller than 4 MB.")
        boundary = "reshow-" + secrets.token_hex(16)
        upload = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="image.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode("ascii") + image + f"\r\n--{boundary}--\r\n".encode("ascii")
        uploaded = tripo_request("/files", token, upload, "multipart/form-data; boundary=" + boundary)
        file_token = uploaded.get("file_token")
        if not isinstance(file_token, str) or not file_token:
            raise RuntimeError("Tripo did not return an image upload token.")
        path = "/generation/image-to-model"
        payload = {"model": TRIPO_MODEL, "input": file_token, "texture": True, "pbr": True, "texture_quality": "standard"}
    created = tripo_request(
        path, token, json.dumps(payload).encode("utf-8"),
    )
    task_id = created.get("task_id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise RuntimeError("Tripo returned no valid task ID. Check task history before retrying; the request may have been charged.")
    return {"task_id": task_id}


def extract_json_object(text):
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and {
            "name",
            "description",
        }.issubset(value):
            name = value["name"]
            description = value["description"]
            if (
                not isinstance(name, str)
                or not isinstance(description, str)
                or not name.strip()
                or not description.strip()
            ):
                raise RuntimeError(
                    "The AI response contains invalid exhibit details. Please try again."
                )
            return name.strip(), description.strip()
    return None


def create_exhibit_name(description):
    first_sentence = re.split(r"(?<=[.!?])\s+", description, maxsplit=1)[0]
    first_sentence = re.sub(
        r"^the image\s+(presents|shows|depicts|features|contains)\s+",
        "",
        first_sentence,
        flags=re.IGNORECASE,
    )
    previous_end = 0
    for word in re.finditer(r"\S+", first_sentence):
        starts_clause = word.group().lower() in ("that", "which", "with")
        if word.group().lower() == "characterized":
            following = first_sentence[word.end():word.end() + 4]
            starts_clause = len(following) == 4 and following[:3].lower() == " by" and following[3].isspace()
        if starts_clause and word.start() > 0 and word.end() < len(first_sentence):
            first_sentence = first_sentence[:previous_end]
            break
        previous_end = word.end()
    name = first_sentence.strip(" \t\n\r.,:;\"'")
    name = re.sub(r"^(a|an|the)\s+", "", name, flags=re.IGNORECASE)
    return name[:1].upper() + name[1:] if name else "Image-based exhibit"


def vision_endpoint(account_id):
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        + account_id
        + "/ai/run/@cf/meta/llama-3.2-11b-vision-instruct"
    )


def describe_result(result):
    # Structural summary only; never echo upstream text or credentials.
    if isinstance(result, dict):
        notes = [
            "result object with keys: " + (", ".join(sorted(result)[:10]) or "(none)")
        ]
        response = result.get("response")
        if isinstance(response, str):
            notes.append("response is a string of length " + str(len(response)))
        elif isinstance(response, dict):
            notes.append(
                "response object with keys: " + (", ".join(sorted(response)[:10]) or "(none)")
            )
        else:
            notes.append("response type: " + type(response).__name__)
        usage = result.get("usage")
        if isinstance(usage, dict):
            completion = usage.get("completion_tokens")
            if isinstance(completion, int):
                notes.append("completion_tokens: " + str(completion))
        calls = result.get("tool_calls")
        if isinstance(calls, list):
            notes.append("tool_calls: " + str(len(calls)))
        return "; ".join(notes)
    if isinstance(result, list):
        return "result list of length " + str(len(result))
    if result is None:
        return "result is null"
    return "result type: " + type(result).__name__


GENERATED_TEXT_KEYS = (
    "response",
    "generated_text",
    "text",
    "content",
    "message",
    "answer",
    "output",
    "arguments",
    "choices",
)


def _find_text(value, key):
    if isinstance(value, dict):
        if key in value and isinstance(value[key], str) and value[key].strip():
            return value[key]
        for item in value.values():
            found = _find_text(item, key)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_text(item, key)
            if found:
                return found
    return None


def extract_generated_text(value):
    # Workers AI returns the generated answer as a plain string, nested inside a
    # chat-style object, or as an already-parsed JSON object. Prefer well-known
    # keys, then fall back to the raw JSON so callers can still locate the object
    # they expect (for example a {"parts": [...]} array).
    if isinstance(value, str):
        return value
    for key in GENERATED_TEXT_KEYS:
        found = _find_text(value, key)
        if found:
            return found
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return None


def generate_vision_text(
    image_bytes,
    account_id,
    api_token,
    prompt,
    max_tokens,
    timeout=40,
    retries=2,
):
    endpoint = vision_endpoint(account_id)
    last_error = None
    for attempt in range(retries + 1):
        try:
            status, response = cloudflare_request(
                endpoint,
                api_token,
                {
                    "prompt": prompt,
                    "image": list(image_bytes),
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
                timeout=timeout,
            )
            if not 200 <= status < 300:
                raise RuntimeError(
                    f"Cloudflare Workers AI rejected the request (HTTP {status}). "
                    "Check that your API token can run Workers AI on this account, "
                    "and that the model is enabled with any required terms accepted."
                )
            if not response.get("success"):
                raise RuntimeError(
                    "Cloudflare Workers AI rejected the request. Check your account, "
                    "token permissions, model access and accepted model terms."
                )

            result = response.get("result")
            generated = extract_generated_text(result)
            if (
                isinstance(generated, str)
                and generated.strip()
                and len(generated) <= 16000
            ):
                return generated.strip()
            last_error = RuntimeError(
                "Cloudflare returned no generated text (" + describe_result(result) + ")."
            )
        except RuntimeError as error:
            last_error = error
        if attempt < retries:
            time.sleep(1 + attempt)
    raise last_error or RuntimeError("Cloudflare Workers AI failed after retries.")


def generate_exhibit(image_bytes, account_id, api_token):
    generated = generate_vision_text(
        image_bytes, account_id, api_token, PROMPT, 512
    )
    details = extract_json_object(generated)
    if details is None:
        description = generated.strip()
        name = create_exhibit_name(description)
    else:
        name, description = details

    return {
        "exhibit_id": "demo-artifact-01",
        "name": name,
        "description": description,
        "tag_id": "DEMO-TAG-001",
        "temperature_min_c": 18,
        "temperature_max_c": 28,
        "brightness_min_raw": 600,
        "brightness_max_raw": 3000,
    }


def invalid_model(reason):
    return RuntimeError(
        "Workers AI returned an invalid 3D model (" + reason + "). Try again."
    )


def preview_text(text):
    collapsed = " ".join(str(text).split())
    return collapsed[:200] + ("..." if len(collapsed) > 200 else "")


def validate_model_parts(parts, minimum, maximum):
    if not isinstance(parts, list):
        raise invalid_model("parts is " + type(parts).__name__ + ", not a list")
    if not minimum <= len(parts) <= maximum:
        raise invalid_model(
            "got "
            + str(len(parts))
            + " parts, expected "
            + str(minimum)
            + " to "
            + str(maximum)
        )

    validated = []
    for index, part in enumerate(parts):
        label = "part " + str(index)
        if not isinstance(part, dict):
            raise invalid_model(label + " is " + type(part).__name__)
        if not {"shape", "position", "scale", "color"}.issubset(part):
            raise invalid_model(label + " keys: " + ", ".join(sorted(part)))
        if part["shape"] not in ("sphere", "box", "cylinder"):
            raise invalid_model(label + " shape: " + str(part["shape"])[:40])
        position = part["position"]
        scale = part["scale"]
        if not isinstance(position, list) or len(position) != 3:
            raise invalid_model(label + " position: " + json.dumps(position)[:60])
        if not isinstance(scale, list) or len(scale) != 3:
            raise invalid_model(label + " scale: " + json.dumps(scale)[:60])
        if any(
            not isinstance(component, (int, float))
            or isinstance(component, bool)
            or not -2.5 <= component <= 2.5
            for component in position
        ):
            raise invalid_model(
                label + " position out of range: " + json.dumps(position)[:60]
            )
        if any(
            not isinstance(component, (int, float))
            or isinstance(component, bool)
            or not 0 < component <= 3
            for component in scale
        ):
            raise invalid_model(
                label + " scale out of range: " + json.dumps(scale)[:60]
            )
        if not isinstance(part["color"], str) or not re.fullmatch(
            r"#[a-fA-F0-9]{6}", part["color"]
        ):
            raise invalid_model(label + " color: " + str(part["color"])[:40])
        validated.append(
            {
                "shape": part["shape"],
                "position": position,
                "scale": scale,
                "color": part["color"],
            }
        )
    if sum(part["shape"] == "sphere" for part in validated) > 3:
        raise RuntimeError("Workers AI returned a sphere-heavy 3D model. Try again.")
    return validated


PART_KEYS = ("shape", "position", "scale", "color")


def salvage_model_parts(text):
    # The free model does not always answer with {"parts": [...]}. It sometimes
    # returns a bare array or a single part, and it often runs out of tokens in
    # the middle of the array. Collect every complete part object so a truncated
    # answer is still usable instead of falling back to the local demo model.
    decoder = json.JSONDecoder()
    parts = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] not in "{[":
            index += 1
            continue
        try:
            candidate, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        if isinstance(candidate, dict) and all(key in candidate for key in PART_KEYS):
            parts.append(candidate)
        elif isinstance(candidate, list):
            parts.extend(
                entry
                for entry in candidate
                if isinstance(entry, dict) and all(key in entry for key in PART_KEYS)
            )
        index += end
    return parts


def extract_model_parts(text, minimum=1, maximum=12):
    decoder = json.JSONDecoder()
    wrapper = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "parts" in candidate:
            wrapper = candidate["parts"]
            break
    if wrapper is not None:
        return validate_model_parts(
            wrapper if isinstance(wrapper, list) else [wrapper], minimum, maximum
        )
    parts = salvage_model_parts(text)
    if parts:
        return validate_model_parts(parts, minimum, maximum)
    raise invalid_model("the response was: " + preview_text(text))


FALLBACK_MODEL_PARTS = [
    {"shape": "box", "position": [0, 0, 0], "scale": [1.4, 0.2, 1.4], "color": "#8b4513"},
    {"shape": "box", "position": [0, 0.75, 0], "scale": [1.0, 1.2, 1.0], "color": "#d2b48c"},
    {"shape": "cylinder", "position": [0, 1.6, 0], "scale": [0.3, 0.3, 0.6], "color": "#8b4513"},
    {"shape": "box", "position": [-0.8, 0.75, 0], "scale": [0.15, 1.2, 1.0], "color": "#5d4037"},
    {"shape": "box", "position": [0.8, 0.75, 0], "scale": [0.15, 1.2, 1.0], "color": "#5d4037"},
    {"shape": "box", "position": [0, 1.9, 0], "scale": [1.3, 0.15, 1.3], "color": "#8b4513"},
    {"shape": "box", "position": [-0.6, 1.25, 0.5], "scale": [0.2, 0.5, 0.1], "color": "#5d4037"},
    {"shape": "box", "position": [0.6, 1.25, 0.5], "scale": [0.2, 0.5, 0.1], "color": "#5d4037"},
    {"shape": "box", "position": [-0.6, 1.25, -0.5], "scale": [0.2, 0.5, 0.1], "color": "#5d4037"},
    {"shape": "box", "position": [0.6, 1.25, -0.5], "scale": [0.2, 0.5, 0.1], "color": "#5d4037"},
    {"shape": "sphere", "position": [0, 2.0, 0], "scale": [0.15, 0.15, 0.15], "color": "#8b4513"},
    {"shape": "cylinder", "position": [0, 0.75, 0.7], "scale": [0.12, 0.12, 0.6], "color": "#d2b48c"},
]


def generate_model_group(image_bytes, account_id, api_token):
    try:
        generated = generate_vision_text(
            image_bytes, account_id, api_token, THREEJS_GROUP_PROMPT, 900, timeout=55
        )
        return {
            "stage": "silhouette",
            "parts": extract_model_parts(generated),
        }
    except Exception as error:
        return {
            "stage": "silhouette",
            "parts": FALLBACK_MODEL_PARTS,
            "fallback": True,
            "reason": str(error),
        }


def generate_model_group_step(
    image_bytes, account_id, api_token, stage, previous_parts
):
    settings = THREEJS_GROUP_STAGES[stage]
    minimum_previous, maximum_previous = settings["previous"]
    try:
        previous_parts = validate_model_parts(
            previous_parts, minimum_previous, maximum_previous
        )
    except Exception:
        previous_parts = list(FALLBACK_MODEL_PARTS[:maximum_previous])
    minimum_new, maximum_new = settings["new"]
    instruction = settings["instruction"]
    prompt = f"""Analyze the attached artifact image and incrementally build a recognizable low-poly Three.js model, not a generic blob.
This is the {stage} step. {instruction}
The existing model parts are: {json.dumps(previous_parts, separators=(",", ":"))}
Return exactly one valid JSON object and no Markdown, using this schema:
{{"parts":[{{"shape":"box","position":[0,0,0],"scale":[1,1,1],"color":"#aabbcc"}}]}}
Return only {minimum_new} to {maximum_new} NEW parts in the parts array; do not repeat the existing parts. Keep the object upright with its main axis on Y and align new parts to the existing coordinates. Each part must have exactly the keys shape, position, scale, and color. shape must be sphere, box, or cylinder. position must contain three numbers from -2.5 to 2.5. scale must contain three numbers greater than 0 and no greater than 3. color must be a six-digit hex color. Prefer boxes and cylinders for structural features. Use spheres only for small rounded details or when the artifact itself is spherical. Match the image's restrained two-to-four color palette. Do not include JavaScript, HTML, explanations, or facts not visible in the image."""
    try:
        generated = generate_vision_text(
            image_bytes, account_id, api_token, prompt, 900, timeout=55
        )
        new_parts = extract_model_parts(generated, minimum_new, maximum_new)
        combined = validate_model_parts(
            previous_parts + new_parts,
            settings["total"][0],
            settings["total"][1],
        )
        return {"stage": stage, "parts": combined, "fallback": False}
    except Exception as error:
        combined = list(previous_parts)
        if len(combined) < settings["total"][0]:
            combined = list(FALLBACK_MODEL_PARTS[: settings["total"][0]])
        return {
            "stage": stage,
            "parts": combined,
            "fallback": True,
            "reason": str(error),
        }


def multipart_message(content_type, body):
    if len(body) > MAX_REQUEST_SIZE:
        raise UploadError(413, "The image must be smaller than 4 MB.")
    if len(content_type) > 256 or "\r" in content_type or "\n" in content_type:
        raise UploadError(400, "The image upload could not be read.")
    if not content_type.lower().startswith("multipart/form-data;"):
        raise UploadError(400, "Choose a JPEG, PNG, or WebP image and try again.")
    header = Message()
    header["Content-Type"] = content_type
    boundary = header.get_boundary()
    if not boundary or not re.fullmatch(r"[A-Za-z0-9'()+_,./:=? -]{1,70}", boundary):
        raise UploadError(400, "The image upload boundary is invalid.")
    if body.count(b"--" + boundary.encode("ascii")) not in (4, 5, 6, 7):
        raise UploadError(400, "Upload exactly one image, account ID and API token.")
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii") + body
        )
    except (LookupError, UnicodeEncodeError) as error:
        raise UploadError(400, "The image upload could not be read.") from error
    if not message.is_multipart() or message.defects:
        raise UploadError(400, "The image upload could not be read.")
    return message


def uploaded_image(part):
    image = part.get_payload(decode=True) or b""
    if not image or len(image) > MAX_IMAGE_SIZE:
        raise UploadError(413, "The image must be smaller than 4 MB.")
    signatures = {
        "image/jpeg": image.startswith(b"\xff\xd8\xff"),
        "image/png": image.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": image.startswith(b"RIFF") and image[8:12] == b"WEBP",
    }
    if not signatures.get(part.get_content_type()):
        raise UploadError(415, "Only JPEG, PNG, and WebP images are supported.")
    return image


def uploaded_credential(part):
    value = part.get_payload(decode=True) or b""
    if len(value) > 4096:
        raise UploadError(400, "A request field is too long.")
    try:
        return value.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise UploadError(400, "Request fields must be text.") from error


def parse_multipart_request(content_type, body):
    fields = {}
    image = None
    seen = set()
    for part in multipart_message(content_type, body).iter_parts():
        name = part.get_param("name", header="content-disposition")
        if part.get_content_disposition() != "form-data" or name not in (
            "image",
            "account_id",
            "api_token",
            "task",
            "stage",
            "previous_parts",
        ) or name in seen:
            raise UploadError(400, "Upload exactly one image, account ID and API token.")
        seen.add(name)
        if name == "image":
            image = uploaded_image(part)
        else:
            fields[name] = uploaded_credential(part)
    account_id = fields.get("account_id", "")
    api_token = fields.get("api_token", "")
    task = fields.get("task", "exhibit")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account_id):
        raise UploadError(400, "Enter a valid 32-character Cloudflare Account ID.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", api_token):
        raise UploadError(400, "Enter a valid Cloudflare API token.")
    if image is None:
        raise UploadError(400, "Choose a JPEG, PNG, or WebP image and try again.")
    if task not in ("exhibit", "threejs_group"):
        raise UploadError(400, "Choose a supported Workers AI generation task.")
    stage = fields.get("stage")
    previous_parts_value = fields.get("previous_parts")
    if task == "exhibit" and (
        stage is not None or previous_parts_value is not None
    ):
        raise UploadError(400, "Choose a supported Workers AI generation task.")
    if stage is None:
        if previous_parts_value is not None:
            raise UploadError(400, "The 3D model step is invalid.")
        previous_parts = None
    else:
        settings = THREEJS_GROUP_STAGES.get(stage)
        if task != "threejs_group" or settings is None:
            raise UploadError(400, "The 3D model step is invalid.")
        try:
            previous_parts_data = json.loads(previous_parts_value or "[]")
        except json.JSONDecodeError as error:
            raise UploadError(400, "The 3D model step is invalid.") from error
        try:
            previous_parts = validate_model_parts(
                previous_parts_data, *settings["previous"]
            )
        except RuntimeError as error:
            raise UploadError(400, "The 3D model step is invalid.") from error
    return image, account_id, api_token, task, stage, previous_parts


class StudioHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=kwargs.pop("directory", str(ROOT)), **kwargs)

    def send_head(self):
        path = unquote(urlsplit(self.path).path)
        parts = Path(path.lstrip("/")).parts
        allowed = path in ("/", "/index.html", "/release.json", "/auth/callback.html") or (
            parts and parts[0] in ("css", "js", "assets")
            and not any(part.startswith(".") for part in parts)
        )
        target = Path(self.translate_path(self.path)).resolve()
        if path == "/":
            target = target / "index.html"
        if not allowed or not target.is_relative_to(Path(self.directory).resolve()) or not target.is_file():
            self.send_error(404)
            return None
        return super().send_head()

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/rfid/status":
            with RFID_EVENT_LOCK:
                status = dict(RFID_WORKFLOW)
                status["latest_event"] = (
                    dict(RFID_LATEST_DEVICE_EVENT)
                    if RFID_LATEST_DEVICE_EVENT is not None
                    else None
                )
                try:
                    description = read_epaper_description()
                    status["epaper_delivery"] = (
                        "confirmed"
                        if EPAPER_DELIVERED_DESCRIPTION == description
                        else "pending"
                    )
                except FileNotFoundError:
                    status["epaper_delivery"] = "not_saved"
                except (OSError, ValueError, KeyError, TypeError):
                    self.send_json(500, {"error": "Could not read the saved e-paper description."})
                    return
            self.send_json(200, {"data": status})
            return
        if path == "/api/rfid/epaper-description":
            with RFID_EVENT_LOCK:
                try:
                    description = read_epaper_description()
                except FileNotFoundError:
                    self.send_json(404, {"error": "No e-paper description has been saved yet."})
                    return
                except (OSError, ValueError, KeyError, TypeError):
                    self.send_json(500, {"error": "Could not read the saved e-paper description."})
                    return
            body = description.encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=us-ascii")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/rfid/"):
            self.send_json(404, {"error": "Not found."})
            return
        super().do_GET()

    def do_POST(self):
        global RFID_LATEST_DEVICE_EVENT, EPAPER_DELIVERED_DESCRIPTION
        path = urlsplit(self.path).path
        if path == "/api/rfid/device-event":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_json(400, {"error": "Invalid request length."})
                return
            if content_length <= 0 or content_length > MAX_RFID_EVENT_SIZE:
                self.send_json(400, {"error": "Invalid RFID device event."})
                return
            try:
                event = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "Invalid RFID device event JSON."})
                return
            if (
                not isinstance(event, dict)
                or not {"type", "uid", "result"} <= set(event)
                or not set(event) <= {"type", "uid", "result", "exhibit_id", "role"}
            ):
                self.send_json(400, {"error": "RFID event must contain type, uid, result, and optional exhibit_id and role."})
                return
            uid = event.get("uid")
            result = event.get("result")
            exhibit_id = event.get("exhibit_id")
            if event.get("type") != "rfid_event":
                self.send_json(400, {"error": "RFID event type must be rfid_event."})
                return
            if not isinstance(uid, str) or not re.fullmatch(r"[A-Fa-f0-9]{4,20}", uid):
                self.send_json(400, {"error": "Invalid RFID UID."})
                return
            if result not in ("accepted", "denied"):
                self.send_json(400, {"error": "RFID result must be accepted or denied."})
                return
            if result == "accepted" and (
                not isinstance(exhibit_id, str)
                or not exhibit_id.strip()
                or len(exhibit_id) > 100
            ):
                self.send_json(400, {"error": "Accepted RFID events require a valid exhibit_id."})
                return
            if exhibit_id is not None and (
                not isinstance(exhibit_id, str) or not exhibit_id.strip() or len(exhibit_id) > 100
            ):
                self.send_json(400, {"error": "Invalid exhibit_id."})
                return
            role = event.get("role")
            if role is not None and role not in ("door", "edit", "epaper"):
                self.send_json(400, {"error": "RFID role must be door, edit or epaper."})
                return
            normalized_event = {
                "type": "rfid_event",
                "uid": uid.upper(),
                "result": result,
            }
            if exhibit_id is not None:
                normalized_event["exhibit_id"] = exhibit_id.strip()
            if role is not None:
                normalized_event["role"] = role
            with RFID_EVENT_LOCK:
                RFID_LATEST_DEVICE_EVENT = normalized_event
                if normalized_event["result"] == "accepted":
                    if role == "edit":
                        if RFID_WORKFLOW["phase"] == "pending_edit":
                            RFID_WORKFLOW.update({"phase": "idle", "uid": None, "exhibit_id": None, "edit_id": None})
                    elif RFID_WORKFLOW["phase"] == "idle":
                        RFID_WORKFLOW.update({
                            "phase": "pending_edit",
                            "uid": normalized_event["uid"],
                            "exhibit_id": normalized_event.get("exhibit_id"),
                            "edit_id": secrets.token_hex(16),
                        })
            self.send_json(200, normalized_event)
            return

        if path == "/api/rfid/publish-description":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > MAX_RFID_EVENT_SIZE:
                    raise ValueError("Invalid request length")
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "Invalid description request."})
                return
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "Invalid description request."})
                return
            edit_id = payload.get("edit_id")
            exhibit_id = payload.get("exhibit_id")
            description = payload.get("description")
            if (not isinstance(exhibit_id, str) or not exhibit_id.strip() or len(exhibit_id) > 100
                    or not isinstance(description, str) or not re.fullmatch(r"[\x20-\x7e]{1,180}", description)
                    or not description.strip()):
                self.send_json(400, {"error": "Select an exhibit and enter 1–180 printable English characters."})
                return
            with RFID_EVENT_LOCK:
                if (RFID_WORKFLOW["phase"] != "pending_edit"
                        or not isinstance(edit_id, str) or edit_id != RFID_WORKFLOW["edit_id"]):
                    self.send_json(409, {"error": "This RFID edit session has ended. Scan a card again."})
                    return
                temp_path = EPAPER_DESCRIPTION_FILE.with_name(EPAPER_DESCRIPTION_FILE.name + "." + secrets.token_hex(8) + ".tmp")
                try:
                    temp_path.write_text(json.dumps({"exhibit_id": exhibit_id, "description": description}), encoding="utf-8")
                    os.replace(temp_path, EPAPER_DESCRIPTION_FILE)
                except OSError:
                    if temp_path.exists():
                        temp_path.unlink()
                    self.send_json(500, {"error": "Could not save the e-paper description."})
                    return
                RFID_WORKFLOW.update({"phase": "idle", "uid": None, "exhibit_id": None, "edit_id": None})
                EPAPER_DELIVERED_DESCRIPTION = None
                workflow = dict(RFID_WORKFLOW)
            self.send_json(200, {"data": workflow})
            return

        if path == "/api/rfid/epaper-delivery":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > 180:
                    raise ValueError("Invalid description length")
                description = self.rfile.read(content_length).decode("ascii")
            except (ValueError, UnicodeDecodeError):
                self.send_json(400, {"error": "Invalid e-paper delivery confirmation."})
                return
            if not re.fullmatch(r"[\x20-\x7e]{1,180}", description):
                self.send_json(400, {"error": "Invalid e-paper delivery confirmation."})
                return
            with RFID_EVENT_LOCK:
                try:
                    current = read_epaper_description()
                except FileNotFoundError:
                    self.send_json(409, {"error": "No description is awaiting delivery."})
                    return
                except (OSError, ValueError, KeyError, TypeError):
                    self.send_json(500, {"error": "Could not read the saved e-paper description."})
                    return
                if current != description:
                    self.send_json(409, {"error": "The saved description has changed."})
                    return
                EPAPER_DELIVERED_DESCRIPTION = description
            self.send_json(200, {"data": {"state": "confirmed"}})
            return

        if path == "/api/technology/agree-terms":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_json(400, {"error": "Invalid request length."})
                return
            if content_length <= 0 or content_length > 8192:
                self.send_json(400, {"error": "Enter the Cloudflare Account ID and API token first."})
                return
            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "Enter the Cloudflare Account ID and API token first."})
                return
            account_id = payload.get("account_id") if isinstance(payload, dict) else None
            api_token = payload.get("api_token") if isinstance(payload, dict) else None
            if not isinstance(account_id, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", account_id):
                self.send_json(400, {"error": "Enter a valid 32-character Cloudflare Account ID."})
                return
            if not isinstance(api_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", api_token):
                self.send_json(400, {"error": "Enter a valid Cloudflare API token."})
                return
            try:
                status, response = cloudflare_request(
                    vision_endpoint(account_id),
                    api_token,
                    {"prompt": "agree"},
                    timeout=30,
                )
                if not 200 <= status < 300 or not response.get("success"):
                    raise RuntimeError(
                        f"Cloudflare Workers AI rejected the model terms (HTTP {status}). "
                        "Check your account, token permissions and model access."
                    )
            except RuntimeError as error:
                self.send_json(502, {"error": str(error)})
                return
            self.send_json(200, {"data": {"agreed": True}})
            return

        if path != "/api/technology/generate-exhibit":
            self.send_json(404, {"error": "Not found."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Invalid request length."})
            return
        if content_length <= 0:
            self.send_json(400, {"error": "Choose an image and try again."})
            return
        if content_length > MAX_REQUEST_SIZE:
            self.send_json(413, {"error": "The image must be smaller than 4 MB."})
            return

        try:
            body = self.rfile.read(content_length)
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() == "application/json":
                self.send_json(200, {"data": process_tripo_request(body)})
                return
            image, account_id, api_token, task, stage, previous_parts = parse_multipart_request(
                self.headers.get("Content-Type", ""),
                body,
            )
            if task == "threejs_group":
                generated = (
                    generate_model_group_step(
                        image,
                        account_id,
                        api_token,
                        stage,
                        previous_parts,
                    )
                    if stage is not None
                    else generate_model_group(image, account_id, api_token)
                )
            else:
                generated = generate_exhibit(image, account_id, api_token)
            self.send_json(
                200, {"data": generated}
            )
        except UploadError as error:
            self.send_json(error.status, {"error": str(error)})
        except RuntimeError as error:
            self.send_json(502, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(
        description="Serve the ReShow dashboard and its 3D/AI studio API."
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--directory", default=str(ROOT))
    parser.add_argument("--cert-file", help="PEM certificate chain for local HTTPS")
    parser.add_argument("--key-file", help="PEM private key for the certificate")
    args = parser.parse_args()
    if bool(args.cert_file) != bool(args.key_file):
        raise SystemExit("Both --cert-file and --key-file are required for HTTPS.")

    if args.cert_file:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=args.cert_file, keyfile=args.key_file)
        try:
            from http.server import ThreadingHTTPSServer
        except ImportError as error:
            raise SystemExit("Local HTTPS hosting requires Python 3.14 or later; the Lambda API supports Python 3.12.") from error
        server = ThreadingHTTPSServer(
            (args.bind, args.port), partial(StudioHandler, directory=args.directory),
            certfile=args.cert_file, keyfile=args.key_file,
        )
        protocol = "HTTPS"
    else:
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(
            (args.bind, args.port), partial(StudioHandler, directory=args.directory)
        )
        protocol = "HTTP"
    try:
        print(
            "ReShow " + protocol + " is listening on " + args.bind + ":" + str(server.server_port),
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ReShow server.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
