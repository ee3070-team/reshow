# Backend Asset Upload: GLB Models and JPG Images

## Goal

Upload an exhibit's `.glb` model and `.jpg` image as binary assets. Keep those
bytes in backend asset storage, and put only stable relative asset paths on the
exhibit entity. Do not put binary data, base64 strings, S3 URLs, or expiring
download URLs in the exhibit JSON.

The relative paths below are logical backend asset references, not paths on the
user's computer and not necessarily physical object-store keys:

```text
assets/exhibits/{exhibit_id}/model.glb
assets/exhibits/{exhibit_id}/image.jpg
```

The backend validates and normalizes these paths, associates each with a
completed asset record, and resolves them when serving asset downloads. Object
storage can remain private; the entity does not expose a bucket URL.

## Existing OpenAPI contract

The supplied `openapi (1).json` describes ReShow Backend API version `0.3.0`.
It already provides a staged private-media upload:

1. `POST /api/media/upload-requests` creates upload metadata and returns a
   temporary S3 form.
2. The client posts the form fields and the file to the returned S3 URL. The
   file must be the last multipart field; do not send the API bearer token to
   S3.
3. `POST /api/media/{media_id}/complete` validates and finalizes the upload.
4. `GET /api/media/{media_id}` returns media metadata and, for ready media, a
   short-lived download URL.

The contract currently accepts JPEG, PNG, and WebP only, limits uploads to
1 MiB, and exposes `image_media_id` on exhibit profiles. It does **not** accept
`model/gltf-binary`, does not expose a model association, and does not expose
logical relative asset paths. Therefore, GLB support and the relative-path
entity design below require an OpenAPI/backend update before clients can rely
on them.

## Recommended entity shape

Add an `assets` object to the exhibit profile input, response, and patch
schemas. Keep the two paths optional so existing exhibits remain valid:

```json
{
  "exhibit_id": "ex-014",
  "name": "Ancient Helmet",
  "description": "A bronze helmet with a decorated crest.",
  "tag_id": "TAG-014",
  "temperature_min_c": 18,
  "temperature_max_c": 28,
  "brightness_min_percent": 20,
  "brightness_max_percent": 80,
  "warning_seconds": 300,
  "assets": {
    "model_path": "assets/exhibits/ex-014/model.glb",
    "image_path": "assets/exhibits/ex-014/image.jpg"
  }
}
```

`model_path` and `image_path` are relative to the backend's logical asset
namespace. A missing asset can be `null` or omitted. The backend should return
these same paths in exhibit-profile responses so the frontend can load them
without constructing storage URLs.

For staged uploads, retain the media ID internally while exposing the logical
path on the entity:

```text
Exhibit entity attribute: assets.model_path
Asset metadata:           path -> media_id, type, content_type, size, status
Private object storage:   media_id -> immutable file bytes
```

The `assets` mapping should only reference assets that have completed upload
and passed backend validation. Replacing a model or image updates that
reference; it should not change the path to an unrelated asset.

## Upload flow

Upload each file independently with its own stable `media_id`. Reuse the same
ID and metadata when retrying the same upload; choose a new ID for different
file content.

### 1. Request an upload form

Example request for a GLB file:

```http
POST /api/media/upload-requests
Authorization: Bearer <api-token>
Content-Type: application/json
```

```json
{
  "media_id": "ex-014-model-v1",
  "content_type": "model/gltf-binary",
  "size_bytes": 382145
}
```

Example request for a JPG image:

```json
{
  "media_id": "ex-014-image-v1",
  "content_type": "image/jpeg",
  "size_bytes": 194832
}
```

The response contains the `media_id` and an `upload` object:

```json
{
  "status": "created",
  "item": {
    "media_id": "ex-014-model-v1",
    "content_type": "model/gltf-binary",
    "size_bytes": 382145,
    "status": "pending"
  },
  "upload": {
    "url": "https://object-storage.example/upload",
    "fields": {
      "key": "<signed staging key>",
      "Content-Type": "model/gltf-binary",
      "policy": "<signed policy>",
      "signature": "<signature>"
    },
    "expires_in_seconds": 300
  }
}
```

The response above is illustrative; use every returned `upload.fields` entry
verbatim. Do not hard-code the signed fields.

### 2. Upload file bytes to the returned URL

Use the returned storage URL, not the ReShow API URL, for this multipart POST.
Append all returned fields first and append the file last:

```js
async function postAssetToStorage(upload, file) {
  const form = new FormData();
  for (const [name, value] of Object.entries(upload.fields)) {
    form.append(name, value);
  }
  form.append("file", file);

  const response = await fetch(upload.url, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error(`Asset upload failed (HTTP ${response.status}).`);
  }
}
```

Do not manually set the multipart `Content-Type` header: the browser must add
the boundary. Do not add the ReShow `Authorization` header to the S3 request.
Treat the temporary URL and signed form fields as secrets; do not log or save
them.

### 3. Complete the upload

After storage confirms the upload, finalize it through the API:

```http
POST /api/media/ex-014-model-v1/complete
Authorization: Bearer <api-token>
Content-Type: application/json
```

```json
{}
```

Do not associate the asset with an exhibit until completion returns a ready
media item. A completion error means the entity must not be updated.

### 4. Associate completed assets with the exhibit

After both media items are ready, create or patch the exhibit profile with
logical relative paths:

```http
PATCH /api/exhibit-profiles/ex-014
Authorization: Bearer <api-token>
Content-Type: application/json
If-Match: <current-profile-version>
```

```json
{
  "assets": {
    "model_path": "assets/exhibits/ex-014/model.glb",
    "image_path": "assets/exhibits/ex-014/image.jpg"
  }
}
```

The current OpenAPI patch schema does not yet include `assets`; add it before
using this request. The backend must confirm that each path resolves to a ready
asset belonging to the caller's exhibit and that the asset's content type
matches its role.

## OpenAPI changes required

Update the spec and backend together:

1. Allow `model/gltf-binary` in `MediaUploadInput.content_type` and
   `MediaItem.content_type`; retain `image/jpeg` for JPG files.
2. Raise or make configurable the current 1 MiB media-size limit if the
   intended GLB files exceed it. Keep a documented server-side maximum.
3. Make completion signature checks accept valid GLB (`glTF` magic bytes),
   JPEG, PNG, and WebP according to the declared media type.
4. Add an `assets` object to `ManagementExhibitInput`,
   `ManagementExhibit`, and `ManagementExhibitPatch`, with nullable optional
   `model_path` and `image_path` strings.
5. Add an asset metadata schema/table mapping each normalized relative path to
   its `media_id`, content type, size, hash, and ready/pending status.
6. Define how clients retrieve an asset by its relative path. The backend can
   resolve the path to private media and return or redirect to a short-lived
   download URL. Never persist that temporary URL in the exhibit entity.
7. Specify authorization, upload expiry, duplicate/retry behavior, path
   normalization, and deletion/orphan-cleanup behavior.

If changing the entity to keep only relative paths is not practical, the
compatible alternative is to add `model_media_id` and use the existing
`image_media_id`. The backend can still resolve those IDs to asset storage,
but that alternative does not put a relative path on the entity as requested.

## Validation and path safety

- Accept only `.glb` as `model/gltf-binary` and `.jpg`/`.jpeg` as
  `image/jpeg`; verify file signatures server-side rather than trusting the
  filename or browser MIME type.
- Generate or normalize asset paths on the server. Reject absolute paths,
  `..`, backslashes, empty path segments, encoded traversal, and paths outside
  the exhibit's asset namespace.
- Enforce maximum byte size, authentication/authorization, content-type and
  exhibit ownership on both upload completion and entity association.
- Use a unique `media_id` per file revision so storage retries are
  idempotent and completed media remains immutable.
- Keep original filenames as optional display metadata only; never use them
  directly as a filesystem path or storage key.
- Do not delete the old asset until the new upload has completed and the
  entity update has succeeded. Clean up unreferenced completed assets through
  a separate retention job.

## Frontend use

The exhibit response returns paths, not file bytes. Render the image with the
backend's relative asset URL and pass the resolved model URL to the GLB loader.
For example:

```js
const imageUrl = `/api/${exhibit.assets.image_path}`;
const modelUrl = `/api/${exhibit.assets.model_path}`;
```

The exact serving route must be defined by the backend and OpenAPI. If the
route returns a redirect or signed URL, use it at load time and do not write
that temporary URL back to the exhibit profile.
