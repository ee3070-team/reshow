import argparse
import base64
import hashlib
import hmac
import json
import re
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
MAX_HUNYUAN_QUERY_SIZE = 8192
RFID_EVENT_FILE = ROOT / "hardware" / "rfid-events.jsonl"
HUNYUAN_SUBMIT_PATH = "/api/technology/hunyuan/submit"
HUNYUAN_QUERY_PATH = "/api/technology/hunyuan/query"
HUNYUAN_REGIONS = {
    "ap-guangzhou",
    "ap-shanghai",
    "ap-nanjing",
    "ap-beijing",
    "ap-chengdu",
    "ap-chongqing",
    "ap-hongkong",
    "ap-singapore",
    "ap-jakarta",
    "ap-bangkok",
    "ap-seoul",
    "ap-tokyo",
    "na-ashburn",
    "na-siliconvalley",
    "sa-saopaulo",
    "eu-frankfurt",
}
RFID_EVENT_LOCK = threading.Lock()
RFID_WORKFLOW = {
    "phase": "idle",
    "uid": None,
    "exhibit_id": None,
}
RFID_LATEST_DEVICE_EVENT = None
PROMPT = """Analyze the attached image and identify the visible artifact. Return exactly one valid JSON object and no Markdown or extra text, using precisely these two keys:
{
  "name": "A short, specific title for the visible image",
  "description": "A concise description of details visible in the image."
}
Do not return a general image summary instead of this JSON. Do not infer facts that cannot be seen."""
THREEJS_GROUP_PROMPT = """Analyze the attached artifact image and build a recognizable low-poly Three.js model from its silhouette and visible details, not a generic blob. Return exactly one valid JSON object and no Markdown, with this schema:
{"parts":[{"shape":"sphere","position":[0,0,0],"scale":[1,1,1],"color":"#aabbcc"}]}
Include 8 to 12 parts. Each part must have exactly the keys shape, position, scale, and color. shape must be sphere, box, or cylinder. position must contain three numbers from -2.5 to 2.5. scale must contain three numbers greater than 0 and no greater than 3. color must be a six-digit hex color. Use boxes and cylinders for the main body, frame, rim, visor, handle, or base. Use at most three spheres, only for small rounded details or when the artifact itself is spherical; never replace a non-spherical artifact with one large sphere. Make parts different sizes and place them to form a clear silhouette instead of stacking them at the origin. Use a restrained palette of two to four colors based on the image. Keep the model upright with its main axis on Y. Do not include JavaScript, HTML, explanations, or facts not visible in the image."""
THREEJS_GROUP_STAGES = {
    "silhouette": {
        "previous": (0, 0),
        "new": (3, 4),
        "total": (3, 4),
        "instruction": "Build only the object's main silhouette from 3 to 4 large parts.",
    },
    "structure": {
        "previous": (3, 4),
        "new": (3, 4),
        "total": (6, 8),
        "instruction": "Add 3 to 4 major structural features that make this specific object recognizable.",
    },
    "details": {
        "previous": (6, 8),
        "new": (2, 4),
        "total": (8, 12),
        "instruction": "Add 2 to 4 small, visible details that refine this specific object.",
    },
}


class UploadError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


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


def validate_hunyuan_credentials(secret_id, secret_key, region):
    if not isinstance(secret_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,128}", secret_id):
        raise UploadError(400, "Enter a valid Tencent Cloud SecretId.")
    if (
        not isinstance(secret_key, str)
        or not secret_key
        or len(secret_key) > 4096
        or "\r" in secret_key
        or "\n" in secret_key
    ):
        raise UploadError(400, "Enter a valid Tencent Cloud SecretKey.")
    if not isinstance(region, str) or region not in HUNYUAN_REGIONS:
        raise UploadError(400, "Choose a supported Tencent Hunyuan region.")


def parse_hunyuan_multipart(content_type, body):
    fields = {}
    image = None
    seen = set()
    for part in multipart_message(content_type, body).iter_parts():
        name = part.get_param("name", header="content-disposition")
        if (
            part.get_content_disposition() != "form-data"
            or name not in ("image", "secret_id", "secret_key", "region")
            or name in seen
        ):
            raise UploadError(400, "Upload exactly one image and Tencent credentials.")
        seen.add(name)
        if name == "image":
            image = uploaded_image(part)
        else:
            fields[name] = uploaded_credential(part)
    if seen != {"image", "secret_id", "secret_key", "region"} or image is None:
        raise UploadError(400, "Upload exactly one image and Tencent credentials.")
    validate_hunyuan_credentials(
        fields["secret_id"], fields["secret_key"], fields["region"]
    )
    return image, fields["secret_id"], fields["secret_key"], fields["region"]


def parse_hunyuan_query(body):
    if not body or len(body) > MAX_HUNYUAN_QUERY_SIZE:
        raise UploadError(400, "The Tencent Hunyuan query is invalid.")
    try:
        fields = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UploadError(400, "The Tencent Hunyuan query is invalid.") from error
    if not isinstance(fields, dict) or set(fields) != {
        "job_id", "secret_id", "secret_key", "region"
    }:
        raise UploadError(400, "The Tencent Hunyuan query is invalid.")
    validate_hunyuan_credentials(
        fields["secret_id"], fields["secret_key"], fields["region"]
    )
    if (
        not isinstance(fields["job_id"], str)
        or not fields["job_id"]
        or len(fields["job_id"]) > 128
        or "\r" in fields["job_id"]
        or "\n" in fields["job_id"]
    ):
        raise UploadError(400, "The Tencent Hunyuan job ID is invalid.")
    return fields["job_id"], fields["secret_id"], fields["secret_key"], fields["region"]


def build_hunyuan_request(action, payload, secret_id, secret_key, region, timestamp=None):
    host = "hunyuan.intl.tencentcloudapi.com"
    service = "hunyuan"
    version = "2023-09-01"
    timestamp = int(time.time()) if timestamp is None else timestamp
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    canonical_headers = (
        "content-type:application/json; charset=utf-8\n"
        f"host:{host}\n"
    )
    signed_headers = "content-type;host"
    canonical_request = "\n".join(
        (
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashlib.sha256(body).hexdigest(),
        )
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        (
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        )
    )
    secret_date = hmac.new(
        ("TC3" + secret_key).encode("utf-8"), date.encode("utf-8"), hashlib.sha256
    ).digest()
    secret_service = hmac.new(
        secret_date, service.encode("utf-8"), hashlib.sha256
    ).digest()
    secret_signing = hmac.new(
        secret_service, b"tc3_request", hashlib.sha256
    ).digest()
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return Request(
        f"https://{host}/",
        data=body,
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Version": version,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Region": region,
        },
        method="POST",
    )


def hunyuan_api_request(action, payload, secret_id, secret_key, region):
    request = build_hunyuan_request(
        action, payload, secret_id, secret_key, region
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=25) as response:
            body = response.read(MAX_RESPONSE_SIZE + 1)
    except HTTPError as error:
        error.close()
        raise RuntimeError(
            "Tencent Hunyuan could not complete the request. Check credentials, permissions, and model access."
        ) from error
    except OSError as error:
        raise RuntimeError(
            "Tencent Hunyuan could not complete the request. Try again."
        ) from error
    if len(body) > MAX_RESPONSE_SIZE:
        raise RuntimeError("Tencent Hunyuan returned an oversized response.")
    result = decode_json_response(body).get("Response")
    if not isinstance(result, dict):
        raise RuntimeError("Tencent Hunyuan returned an invalid response.")
    if isinstance(result.get("Error"), dict):
        raise RuntimeError(
            "Tencent Hunyuan rejected the request. Check credentials, permissions, and model access."
        )
    return result


def submit_hunyuan_job(image, secret_id, secret_key, region):
    result = hunyuan_api_request(
        "SubmitHunyuanTo3DRapidJob",
        {
            "ImageBase64": base64.b64encode(image).decode("ascii"),
            "ResultFormat": "GLB",
        },
        secret_id,
        secret_key,
        region,
    )
    job_id = result.get("JobId")
    if not isinstance(job_id, str) or not job_id or len(job_id) > 128:
        raise RuntimeError("Tencent Hunyuan returned no valid job ID.")
    return {"job_id": job_id}


def query_hunyuan_job(job_id, secret_id, secret_key, region):
    result = hunyuan_api_request(
        "QueryHunyuanTo3DRapidJob",
        {"JobId": job_id},
        secret_id,
        secret_key,
        region,
    )
    status = result.get("Status")
    if status not in ("WAIT", "RUN", "FAIL", "DONE"):
        raise RuntimeError("Tencent Hunyuan returned an unknown job status.")
    files = []
    if status == "DONE":
        result_files = result.get("ResultFile3Ds")
        if isinstance(result_files, list):
            for item in result_files:
                if not isinstance(item, dict):
                    continue
                url = item.get("Url")
                if not isinstance(url, str):
                    continue
                parsed_url = urlsplit(url)
                if (
                    parsed_url.scheme != "https"
                    or not parsed_url.hostname
                    or parsed_url.username
                    or parsed_url.password
                ):
                    continue
                file = {"url": url}
                file_type = item.get("Type")
                if isinstance(file_type, str) and len(file_type) <= 64:
                    file["type"] = file_type
                files.append(file)
    return {"job_id": job_id, "status": status, "files": files}


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


def generate_vision_text(
    image_bytes,
    account_id,
    api_token,
    prompt,
    max_tokens,
    timeout=40,
    retries=2,
):
    endpoint = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + account_id
        + "/ai/run/@cf/meta/llama-3.2-11b-vision-instruct"
    )
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
            generated = (
                result.get("response") if isinstance(result, dict) else None
            )
            if (
                isinstance(generated, str)
                and generated.strip()
                and len(generated) <= 16000
            ):
                return generated.strip()
            last_error = RuntimeError("Cloudflare returned no generated text.")
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


def validate_model_parts(parts, minimum, maximum):
    if not isinstance(parts, list) or not minimum <= len(parts) <= maximum:
        raise RuntimeError("Workers AI returned an invalid 3D model. Try again.")

    validated = []
    for part in parts:
        if not isinstance(part, dict) or set(part) != {
            "shape",
            "position",
            "scale",
            "color",
        }:
            raise RuntimeError("Workers AI returned an invalid 3D model. Try again.")
        if part["shape"] not in ("sphere", "box", "cylinder"):
            raise RuntimeError("Workers AI returned an invalid 3D model. Try again.")
        position = part["position"]
        scale = part["scale"]
        if (
            not isinstance(position, list)
            or len(position) != 3
            or not isinstance(scale, list)
            or len(scale) != 3
            or any(
                not isinstance(component, (int, float))
                or isinstance(component, bool)
                or not -2.5 <= component <= 2.5
                for component in position
            )
            or any(
                not isinstance(component, (int, float))
                or isinstance(component, bool)
                or not 0 < component <= 3
                for component in scale
            )
            or not isinstance(part["color"], str)
            or not re.fullmatch(r"#[a-fA-F0-9]{6}", part["color"])
        ):
            raise RuntimeError("Workers AI returned an invalid 3D model. Try again.")
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


def extract_model_parts(text, minimum=8, maximum=12):
    decoder = json.JSONDecoder()
    value = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "parts" in candidate:
            value = candidate
            break
    parts = value.get("parts") if isinstance(value, dict) else None
    return validate_model_parts(parts, minimum, maximum)


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
    except Exception:
        return {
            "stage": "silhouette",
            "parts": FALLBACK_MODEL_PARTS,
            "fallback": True,
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
            image_bytes, account_id, api_token, prompt, 650, timeout=55
        )
        new_parts = extract_model_parts(generated, minimum_new, maximum_new)
        combined = validate_model_parts(
            previous_parts + new_parts,
            settings["total"][0],
            settings["total"][1],
        )
        return {"stage": stage, "parts": combined, "fallback": False}
    except Exception:
        combined = list(previous_parts)
        if len(combined) < settings["total"][0]:
            combined = list(FALLBACK_MODEL_PARTS[: settings["total"][0]])
        return {
            "stage": stage,
            "parts": combined,
            "fallback": True,
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
        allowed = path in ("/", "/index.html", "/release.json") or (
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
            self.send_json(200, {"data": status})
            return
        if path == "/api/rfid/device-event":
            with RFID_EVENT_LOCK:
                event = dict(RFID_LATEST_DEVICE_EVENT) if RFID_LATEST_DEVICE_EVENT else None
            if event is None:
                self.send_json(404, {"error": "No RFID device event has been received yet."})
                return
            self.send_json(200, event)
            return
        super().do_GET()

    def do_POST(self):
        global RFID_LATEST_DEVICE_EVENT
        path = urlsplit(self.path).path
        if path in (HUNYUAN_SUBMIT_PATH, HUNYUAN_QUERY_PATH):
            origin = urlsplit(self.headers.get("Origin", ""))
            scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
            host = self.headers.get("Host", "").lower()
            if (
                origin.scheme != scheme
                or origin.netloc.lower() != host
                or origin.path not in ("", "/")
                or origin.query
                or origin.fragment
            ):
                self.send_json(403, {"error": "Use the ReShow website to submit Tencent Hunyuan jobs."})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_json(400, {"error": "Invalid request length."})
                return
            maximum = MAX_REQUEST_SIZE if path == HUNYUAN_SUBMIT_PATH else MAX_HUNYUAN_QUERY_SIZE
            if content_length <= 0 or content_length > maximum:
                self.send_json(413, {"error": "The Tencent Hunyuan request is too large."})
                return
            body = self.rfile.read(content_length)
            try:
                if path == HUNYUAN_SUBMIT_PATH:
                    result = submit_hunyuan_job(
                        *parse_hunyuan_multipart(
                            self.headers.get("Content-Type", ""), body
                        )
                    )
                else:
                    result = query_hunyuan_job(*parse_hunyuan_query(body))
                self.send_json(200, {"data": result})
            except UploadError as error:
                self.send_json(error.status, {"error": str(error)})
            except RuntimeError as error:
                self.send_json(502, {"error": str(error)})
            return

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
            if not isinstance(event, dict) or set(event) not in (
                {"type", "uid", "result"},
                {"type", "uid", "result", "exhibit_id"},
            ):
                self.send_json(400, {"error": "RFID event must contain type, uid, result, and optional exhibit_id."})
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
            normalized_event = {
                "type": "rfid_event",
                "uid": uid.upper(),
                "result": result,
            }
            if exhibit_id is not None:
                normalized_event["exhibit_id"] = exhibit_id.strip()
            with RFID_EVENT_LOCK:
                RFID_LATEST_DEVICE_EVENT = normalized_event
            self.send_json(200, normalized_event)
            return

        if path == "/api/rfid/access-event":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_json(400, {"error": "Invalid request length."})
                return
            if content_length <= 0 or content_length > MAX_RFID_EVENT_SIZE:
                self.send_json(400, {"error": "Invalid RFID event."})
                return
            try:
                event = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "Invalid RFID event JSON."})
                return
            uid = event.get("uid") if isinstance(event, dict) else None
            result = event.get("result") if isinstance(event, dict) else None
            door = event.get("door") if isinstance(event, dict) else None
            phase = event.get("phase") if isinstance(event, dict) else None
            exhibit_id = event.get("exhibit_id") if isinstance(event, dict) else None
            if not isinstance(uid, str) or not re.fullmatch(r"[A-Fa-f0-9]{4,20}", uid):
                self.send_json(400, {"error": "Invalid RFID UID."})
                return
            if result not in ("accepted", "denied") or door not in ("opened", "closed"):
                self.send_json(400, {"error": "Invalid RFID event result."})
                return
            if phase not in ("first_scan_pending", "second_scan_confirmed", "pending_edit_waiting", "denied"):
                self.send_json(400, {"error": "Invalid RFID workflow phase."})
                return
            with RFID_EVENT_LOCK:
                if phase == "first_scan_pending":
                    if RFID_WORKFLOW["phase"] != "idle":
                        self.send_json(409, {"error": "An exhibit edit is already pending."})
                        return
                    if not isinstance(exhibit_id, str) or not exhibit_id.strip():
                        self.send_json(400, {"error": "ESP32 must provide the exhibit ID for the first scan."})
                        return
                    RFID_WORKFLOW.update({
                        "phase": "pending_edit",
                        "uid": uid.upper(),
                        "exhibit_id": exhibit_id.strip(),
                    })
                elif phase == "second_scan_confirmed":
                    if (
                        RFID_WORKFLOW["phase"] != "ready_to_confirm"
                        or RFID_WORKFLOW["uid"] != uid.upper()
                    ):
                        self.send_json(409, {"error": "The second scan is not confirmed by staff."})
                        return
                    RFID_WORKFLOW.update({
                        "phase": "idle",
                        "uid": uid.upper(),
                    })
                    RFID_WORKFLOW["exhibit_id"] = None
                record = {
                    "uid": uid.upper(),
                    "result": result,
                    "door": door,
                    "phase": phase,
                    "exhibit_id": exhibit_id.strip() if isinstance(exhibit_id, str) else RFID_WORKFLOW["exhibit_id"],
                }
                with RFID_EVENT_FILE.open("a", encoding="utf-8") as event_file:
                    event_file.write(json.dumps(record, separators=(",", ":")) + "\n")
            with RFID_EVENT_LOCK:
                workflow = dict(RFID_WORKFLOW)
            self.send_json(200, {"data": {"recorded": True, **workflow}})
            return

        if path == "/api/rfid/staff-ready":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "Invalid staff confirmation message."})
                return
            with RFID_EVENT_LOCK:
                if RFID_WORKFLOW["phase"] != "pending_edit":
                    self.send_json(409, {"error": "No RFID exhibit edit is pending."})
                    return
                if not isinstance(RFID_WORKFLOW["exhibit_id"], str) or not RFID_WORKFLOW["exhibit_id"]:
                    self.send_json(409, {"error": "The ESP32 has not assigned an exhibit ID."})
                    return
                RFID_WORKFLOW.update({
                    "phase": "ready_to_confirm",
                })
                workflow = dict(RFID_WORKFLOW)
            self.send_json(200, {"data": workflow})
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
            image, account_id, api_token, task, stage, previous_parts = parse_multipart_request(
                self.headers.get("Content-Type", ""),
                self.rfile.read(content_length),
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
