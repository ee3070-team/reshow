"""HTTPS-only CloudFront/Lambda adapter for the visitor's Cloudflare BYOK request."""
import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from server import MAX_REQUEST_SIZE, UploadError, generate_exhibit, parse_multipart_request

GENERATE_PATH = "/api/technology/generate-exhibit"


def response(status, data):
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store, private",
            "x-content-type-options": "nosniff",
        },
        "body": json.dumps(data),
    }


def health():
    try:
        commit = json.loads(Path(__file__).with_name("release.json").read_text())["commit"]
    except (OSError, ValueError, KeyError):
        return response(503, {"error": "Release metadata is unavailable."})
    return response(200, {"commit": commit, "service": "reshow-studio-api"})


def request_body(event):
    body = event.get("body", "")
    if not isinstance(body, str) or len(body) > 4 * ((MAX_REQUEST_SIZE + 2) // 3):
        raise UploadError(413, "The image must be smaller than 4 MB.")
    try:
        decoded = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") else body.encode("utf-8")
    except (binascii.Error, UnicodeError) as error:
        raise UploadError(400, "The image upload could not be read.") from error
    if not decoded or len(decoded) > MAX_REQUEST_SIZE:
        raise UploadError(413, "The image must be smaller than 4 MB.")
    return decoded


def generate(event):
    headers = {key.lower(): value for key, value in event.get("headers", {}).items()}
    allowed_origin = os.environ.get("RESHOW_ALLOWED_ORIGIN")
    if not allowed_origin:
        return response(503, {"error": "The API origin is not configured."})
    if headers.get("origin") != allowed_origin:
        return response(403, {"error": "Use the ReShow website to generate exhibit data."})
    try:
        body = request_body(event)
        # CloudFront Lambda OAC requires this header for POST requests. Verify the
        # same serialized bytes the browser hashed, without logging their content.
        digest = hashlib.sha256(body).hexdigest()
        checksum = headers.get("x-amz-content-sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", checksum) or not hmac.compare_digest(checksum, digest):
            raise UploadError(400, "The image upload checksum is invalid.")
        image, account, token = parse_multipart_request(headers.get("content-type", ""), body)
        return response(200, {"data": generate_exhibit(image, account, token)})
    except UploadError as error:
        return response(error.status, {"error": str(error)})
    except RuntimeError:
        # Neither upstream error bodies nor credentials enter Lambda logs/results.
        return response(502, {"error": "Cloudflare Workers AI could not complete the request. Check your credentials and model access in Cloudflare."})


def handler(event, _context):
    method = event.get("requestContext", {}).get("http", {}).get("method")
    path = event.get("rawPath")
    if path == "/api/health" and method == "GET":
        return health()
    if path == GENERATE_PATH:
        if method == "POST":
            return generate(event)
        return response(405, {"error": "Use POST to generate exhibit data."})
    return response(404, {"error": "Not found."})
