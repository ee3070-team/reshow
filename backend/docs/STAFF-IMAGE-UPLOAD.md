# ReShow staff image upload

Use `https://urauw4ckw1.execute-api.us-east-1.amazonaws.com` for the API. Images are PNG (`image/png`), JPEG (`image/jpeg`) or WebP (`image/webp`) and must be **1–1,048,576 bytes inclusive**. Finalization also requires a recognizable signature, so a one-byte object cannot become a ready image. Signature verification is a format check and does not decode the full image. Keep image bytes in private S3 and store only the stable `image_media_id` on exhibit and label records.

## Authentication and origins

Obtain a Cognito access token using the hosted authorization-code flow with S256 PKCE and `reshow/staff` scope. Use [public sign-in configuration](https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/auth/config). Tokens last 900 seconds. Send `Authorization: Bearer <staff-access-token>` to the staff API and use `Content-Type: application/json` for JSON bodies. Keep credentials in memory. The API checks the current account status and role on each request and derives audit identity from the session. Omit `actor`.

Administrator and Operator can request uploads, finalize and change exhibit or draft associations. Viewer can read media and records. Media is shared among authorized staff under the existing role policy. Producer tokens cannot use staff media management. The API checks access before issuing a signed capability. A capability already issued can remain usable until its S3 expiry, but finalization and association still require a currently authorized session. The legacy `/api/media/…` paths are retired for website use.

| Service | Allowed origins | Methods and headers |
| --- | --- | --- |
| Staff API | `http://localhost:8000`, `https://dkbznu9f5nh9n.cloudfront.net` | GET, POST, PATCH, PUT, OPTIONS. `Authorization`, `Content-Type`, `If-Match`. The response exposes `ETag`. |
| Private S3 | Same two origins | POST, GET, HEAD. Allowed request headers are `Content-Type` and `x-amz-*`. The response exposes `ETag`. The browser needs no custom headers for the signed operations. |

The Cognito allowlist includes `/auth/callback.html` on both origins. The frontend checkout's default local server uses `https://127.0.0.1:8000`, which is a different origin. Use the approved HTTP localhost harness for development sign-in checks.

## Upload and readback

1. Send `POST /staff-api/media/upload-requests` with `{"media_id":"exhibit-img-01","content_type":"image/png","size_bytes":68}`. Choose a case-sensitive 1–64 character ID matching `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`. Generate one ID per selected file revision and keep it for retries of the same file.
2. A new request returns HTTP 201 with `status: "created"`, a pending `item` and `upload: { url, fields, expires_in_seconds }`. An identical request returns HTTP 200 with `status: "duplicate"`. A pending duplicate includes a fresh signed form. A ready duplicate includes `item.sha256` and omits `upload`. Skip the S3 POST and completion for a ready duplicate. Compare the stored SHA-256 to the selected bytes before associating a reused ID.
3. For pending media, create `FormData`. Append **all** `upload.fields` exactly as received, then append the file under `file` as the final field. POST to the exact `upload.url` over HTTPS. Let the browser set the multipart boundary. Send neither the API bearer token nor cookies to S3. Accept a 2xx response before completing, typically 204 with no body. The form expires in 300 seconds and binds the staging key, declared Content-Type and exact byte count.
4. Send `POST /staff-api/media/{media_id}/complete` with `{}` and the staff access token. HTTP 200 returns `{"item":{…}}` with `item.status: "ready"` and `item.sha256`. The backend validates exact size, stored Content-Type and the format signature, then saves verified bytes at an immutable S3 key before committing ready metadata. A retained staging form can only write to staging. Ready completion repeats return the committed item.
5. Associate the ready ID using the relevant management operation below. For readback, send `GET /staff-api/media/{media_id}` with a staff access token. Pending media returns only `item`. Ready media also returns `download: { url, expires_in_seconds: 300 }`. Use the signed URL as returned for an S3 GET without API credentials. Request a fresh URL from the staff API when needed. Treat signed forms and URLs as temporary credentials and keep them out of logs, persistent storage, and record fields.

Example HTTP 201 response. The form URL and fields below are placeholders. Always submit the full actual field map.

```json
{
  "status": "created",
  "item": {
    "media_id": "exhibit-img-01",
    "content_type": "image/png",
    "size_bytes": 68,
    "status": "pending",
    "version": 1,
    "created_at": "2026-11-10T06:00:00.000000Z",
    "updated_at": "2026-11-10T06:00:00.000000Z"
  },
  "upload": {
    "url": "https://example.invalid/s3-upload",
    "fields": {"key": "<staging-key>", "policy": "<signed-policy>"},
    "expires_in_seconds": 300
  }
}
```

Example HTTP 200 response from the metadata GET after completion. Completion returns the same ready item shape without `download`.

```json
{
  "item": {
    "media_id": "exhibit-img-01",
    "content_type": "image/png",
    "size_bytes": 68,
    "status": "ready",
    "sha256": "<64-lowercase-hex-characters>",
    "version": 2,
    "created_at": "2026-11-10T06:00:00.000000Z",
    "updated_at": "2026-11-10T06:00:02.000000Z"
  },
  "download": {
    "url": "https://example.invalid/s3-download",
    "expires_in_seconds": 300
  }
}
```

## Exhibit and label associations

| Action | Request | Image and version rules |
| --- | --- | --- |
| Create exhibit profile | `POST /staff-api/exhibit-profiles` | Submit the normal profile fields and optionally `image_media_id`. Omitted or null means no image. No `If-Match` on create. An identical create retry may return 200 with `status: "duplicate"`. |
| Update exhibit profile | `PATCH /staff-api/exhibit-profiles/{exhibit_id}` | Send `If-Match: <profile-version>` from the profile read or its ETag. Body `{"image_media_id":"exhibit-img-01"}` replaces the image. Omit the field on text edits to preserve the current image. Send `{"image_media_id":null}` to clear it. |
| Save label draft | `PUT /staff-api/label-drafts/{exhibit_id}` | Send `If-Match: 0` for the first draft or the current **draft** version for an edit. Send complete content. For example, `{"title":"Vase","collection":"Ceramics","description":"Stoneware","image_media_id":"label-img-01"}`. Resend the current ID for text edits, or pass null for text-only content or intentional removal. |

Get the current versions with `GET /staff-api/exhibit-profiles/{exhibit_id}` or `GET /staff-api/label-drafts/{exhibit_id}`. Both wrap the record in `item` and expose its version. The exhibit reference is `item.image_media_id` and the draft reference is `item.content.image_media_id`. Keep the current record image through replacement upload and finalization, then change the UI only after the record update succeeds. Use the version from when the edit started. On conflict, reload and reconcile before submitting another write.

Exhibit and draft references are independent. Draft save changes the draft only. Publishing a physical label uses the existing `POST /staff-api/commands` label command with `exhibit_id` and `draft_version` in its payload, then the separate producer acknowledgement workflow. Show a confirmed display state only when the existing command/read models report it.

## Recovery and errors

| Status and code | Handling |
| --- | --- |
| API 400 `invalid_payload` or `invalid_json` | Correct the metadata, ID, size, type, JSON or request fields. Completion expects an empty JSON object. |
| API 400 `invalid_asset` | Staging bytes, Content-Type or signature failed verification. The media stays pending. Reupload the original valid file using the same ID and declared metadata, or start a new ID for a different revision. |
| API 401 `unauthorized` or `session_expired` | Sign in again. The gateway may return `{"message":"Unauthorized"}` instead of the application error envelope. |
| API 403 `forbidden` or `account_unavailable` | Stop the write. Check the current staff role or account state. A producer token used at the JWT gateway can receive 401 before Lambda. |
| API 404 `not_found` | The media ID or requested profile/draft does not exist. An unknown media ID cannot be associated. |
| API 409 `id_conflict` | The media ID belongs to different metadata. Use a new ID for a different revision. |
| API 409 `upload_incomplete` or `upload_changed` | The staged object is unavailable or changed during verification. Check current media status, then retry completion or obtain a new form and send the same file. |
| API 409 `media_not_ready` | Finalize the pending media before associating it. |
| API 409 `version_conflict` | Read the current state. For an exhibit or draft, reconcile edits before retrying. For concurrent completion, compare the ready item's digest to the intended bytes. |
| API 415 `unsupported_media_type` or 428 `precondition_required` | Supply a JSON request or a valid numeric `If-Match` on the versioned record write. |
| API 429 or 503 | Back off. Check current state after an ambiguous write. Repeat media requests with the same ID and identical file. A final-object integrity error is a service failure. |
| S3 non-2xx or fetch rejection | The form may have expired, the upload may have been interrupted, or the policy may have rejected it. If success is ambiguous, check media state and try completion. If there is no verifiable object, obtain a new form and send the same file. S3 errors do not use the staff API envelope. |

Application errors use `{"error":{"code":"…","message":"…"}}`. After an ambiguous association write, read the record and compare its version and content before retrying. A first draft PUT with `If-Match: 0` conflicts once the draft exists, even if the original request succeeded. If media is already ready after an ambiguous upload or finalization, use the ready item's digest to confirm it and skip the upload. For expired downloads, fetch fresh media metadata once before retrying the S3 GET.

The deployed bucket has no lifecycle rules, and the application has no automated staging, pending-metadata or orphan cleanup. Replacements and removals retain prior media. A follow-up can introduce a staging-only expiration rule and separately design reference-aware cleanup. Keep final objects while referenced by exhibits, label drafts, pending commands, acknowledged labels, or saved snapshots.

## Browser example

Pass the existing in-memory access-token getter, a selected `File`, a stable ID, and either the exhibit profile version or the full label content and draft version. Reuse the ID only with the same file. This example performs one attempt and returns the saved record and verified bytes. Wire the recovery paths above into the UI. To display verified bytes, create an object URL from a Blob with the selected file type and revoke it when the image view is disposed.

```javascript
const apiBase = "https://urauw4ckw1.execute-api.us-east-1.amazonaws.com";
const types = new Set(["image/png", "image/jpeg", "image/webp"]);

async function sha256(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

async function uploadImage({getAccessToken, file, mediaId, target}) {
  if (!types.has(file.type) || file.size < 1 || file.size > 1048576) {
    throw new Error("Choose PNG, JPEG or WebP, up to 1 MiB.");
  }
  const expectedHash = await sha256(await file.arrayBuffer());
  const id = encodeURIComponent(mediaId);
  const exhibitId = encodeURIComponent(target.exhibitId);

  async function api(path, {method = "GET", body, version} = {}) {
    const token = await getAccessToken();
    const headers = {Authorization: `Bearer ${token}`};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (version !== undefined) headers["If-Match"] = String(version);
    const response = await fetch(apiBase + "/staff-api" + path, {
      method, headers, credentials: "omit", body: JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = data.error?.code ?? "request_failed";
      throw Object.assign(new Error(code), {status: response.status, code});
    }
    return data;
  }

  const request = await api("/media/upload-requests", {
    method: "POST",
    body: {media_id: mediaId, content_type: file.type, size_bytes: file.size}
  });
  let item = request.item;

  if (item.status === "pending") {
    const form = new FormData();
    for (const [key, value] of Object.entries(request.upload.fields)) {
      form.append(key, value);
    }
    form.append("file", file);
    const staged = await fetch(request.upload.url, {
      method: "POST", body: form, credentials: "omit", referrerPolicy: "no-referrer"
    });
    if (!staged.ok) throw new Error("S3 upload did not succeed.");
    ({item} = await api(`/media/${id}/complete`, {method: "POST", body: {}}));
  }

  // A ready duplicate arrives here without uploading or completing again.
  if (item.status !== "ready" || item.sha256 !== expectedHash) {
    throw new Error("The ready media does not match the selected file.");
  }

  const association = target.kind === "label"
    ? await api(`/label-drafts/${exhibitId}`, {
        method: "PUT", version: target.version,
        body: {...target.content, image_media_id: mediaId}
      })
    : await api(`/exhibit-profiles/${exhibitId}`, {
        method: "PATCH", version: target.version, body: {image_media_id: mediaId}
      });

  const {download} = await api(`/media/${id}`);
  const response = await fetch(download.url, {
    credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer"
  });
  if (!response.ok) throw new Error("The image was saved. Retry its readback.");
  const bytes = await response.arrayBuffer();
  if (await sha256(bytes) !== expectedHash) {
    throw new Error("The downloaded bytes did not match the saved image.");
  }
  return {record: association.item, bytes};
}
```
