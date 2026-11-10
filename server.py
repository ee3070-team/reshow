import argparse
import json
import re
import ssl
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
PROMPT = """Analyze the attached image and identify the visible artifact. Return exactly one valid JSON object and no Markdown or extra text, using precisely these two keys:
{
  "name": "A short, specific title for the visible image",
  "description": "A concise description of details visible in the image."
}
Do not return a general image summary instead of this JSON. Do not infer facts that cannot be seen."""


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


def cloudflare_request(endpoint, token, payload):
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
        with build_opener(NoRedirects()).open(request, timeout=40) as response:
            return response.status, read_cloudflare_response(response)
    except HTTPError as error:
        # Upstream bodies may echo credentials. Do not return or log them.
        return error.code, {}
    except OSError as error:
        raise RuntimeError("Cloudflare Workers AI could not complete the request. Try again.") from error


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


def generate_exhibit(image_bytes, account_id, api_token):
    endpoint = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + account_id
        + "/ai/run/@cf/meta/llama-3.2-11b-vision-instruct"
    )
    status, response = cloudflare_request(
        endpoint,
        api_token,
        {
            "prompt": PROMPT,
            "image": list(image_bytes),
            "max_tokens": 512,
            "temperature": 0.2,
        },
    )
    if not (200 <= status < 300 and response.get("success")):
        raise RuntimeError(
            "Cloudflare Workers AI rejected the request. Check your account, token, "
            "model access and accepted model terms in Cloudflare."
        )

    result = response.get("result")
    generated = result.get("response") if isinstance(result, dict) else None
    if not isinstance(generated, str) or not generated.strip() or len(generated) > 16000:
        raise RuntimeError("Cloudflare returned no generated text. Try another image.")
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
    if body.count(b"--" + boundary.encode("ascii")) != 4:
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
        raise UploadError(400, "Cloudflare credentials are too long.")
    try:
        return value.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise UploadError(400, "Cloudflare credentials must be text.") from error


def parse_multipart_request(content_type, body):
    fields = {}
    image = None
    seen = set()
    for part in multipart_message(content_type, body).iter_parts():
        name = part.get_param("name", header="content-disposition")
        if part.get_content_disposition() != "form-data" or name not in ("image", "account_id", "api_token") or name in seen:
            raise UploadError(400, "Upload exactly one image, account ID and API token.")
        seen.add(name)
        if name == "image":
            image = uploaded_image(part)
        else:
            fields[name] = uploaded_credential(part)
    account_id = fields.get("account_id", "")
    api_token = fields.get("api_token", "")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account_id):
        raise UploadError(400, "Enter a valid 32-character Cloudflare Account ID.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", api_token):
        raise UploadError(400, "Enter a valid Cloudflare API token.")
    if image is None:
        raise UploadError(400, "Choose a JPEG, PNG, or WebP image and try again.")
    return image, account_id, api_token


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

    def do_POST(self):
        if self.path != "/api/technology/generate-exhibit":
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
            image, account_id, api_token = parse_multipart_request(
                self.headers.get("Content-Type", ""),
                self.rfile.read(content_length),
            )
            self.send_json(
                200, {"data": generate_exhibit(image, account_id, api_token)}
            )
        except UploadError as error:
            self.send_json(error.status, {"error": str(error)})
        except RuntimeError:
            self.send_json(502, {"error": "Cloudflare Workers AI could not complete the request. Check your credentials and model access in Cloudflare."})



def main():
    parser = argparse.ArgumentParser(
        description="Serve the ReShow dashboard and its 3D/AI studio API."
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--directory", default=str(ROOT))
    parser.add_argument("--cert-file", required=True, help="PEM certificate chain for local HTTPS")
    parser.add_argument("--key-file", required=True, help="PEM private key for the certificate")
    args = parser.parse_args()
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
    try:
        print(
            "ReShow HTTPS is listening on " + args.bind + ":" + str(server.server_port),
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ReShow server.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
