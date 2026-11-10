# Exhibit and label images

In the Live workspace, choose an image in an exhibit profile or electronic-label
editor, then save. The preview remains an unsaved edit until the upload is verified
and the record is saved. Removing an image requires saving the edit as well.
PNG, JPEG and WebP are supported, from 1 through 1,048,576 bytes; the backend also
checks the file signature. Administrators and Operators may upload. Viewers can
read existing images. Demo mode continues to save its images locally.

## Integration contract

The public OpenAPI 0.4.1 was checked against the teammate's 10 November 2026
release package. Parsed contracts match, including the corrected staff completion
route. The runtime uses the existing shared staff session and live adapter:

1. `uploadImage(file, { onProgress, signal })` requests
   `POST /staff-api/media/upload-requests` with a stable `media_id`, `content_type`
   and `size_bytes`. Retrying the same selected File retains its media ID in memory.
2. Pending media supplies a signed form. Send every field unchanged and append the
   file last to the returned HTTPS S3 URL. No staff credentials or cookies go to S3.
3. `POST /staff-api/media/{media_id}/complete` with `{}` finalizes the object.
   Compare its SHA-256 with the selected bytes. An already-ready duplicate skips
   upload and completion but must pass the same digest check.
4. Save the returned `imageMediaId` as `image_media_id` using the existing profile
   PATCH/create or label-draft PUT. Retain the editor's original record version.
   Local previews and file bytes never enter the record JSON.
5. Existing `GET /staff-api/media/{media_id}` handling obtains temporary download
   URLs for display. Both upload forms and download URLs expire after 300 seconds.

The adapter enforces permissions and invalidates upload work when the signed-in
identity changes. Editors keep selected files in memory only and prevent navigation
while saving. Record conflicts retain the user's edits for review; do not silently
replace the opened version with a newer version. An ambiguous association write
can be reconciled only against matching saved content and the expected next version.

Exhibit and label-draft image references are independent. Sending a label saves its
draft first, then requests the existing device command. An accepted draft or command
is not evidence that the physical display changed. A failed command leaves the
saved draft available to retry.

## Browser origins and storage

The backend release permits `http://localhost:8000` and
`https://dkbznu9f5nh9n.cloudfront.net` for staff API and S3 access. Our standard local
server uses HTTPS. Real local staff login/uploads therefore require an explicitly
configured HTTPS origin or a separate approved HTTP localhost test harness; a
different scheme or hostname is not equivalent. The isolated browser regression
fixture uses in-memory API responses and never sends a production upload.

The backend retains old images and currently has no automated staging/orphan
cleanup. The frontend never deletes media objects: an old image may still be used
by drafts, commands, acknowledged labels or saved reports. Backend cleanup is a
separate reference-aware maintenance task.

## Verification

`npm test` includes upload transport and retry tests, page-handler tests, navigation
guards and a real Chromium DOM fixture. The fixture verifies selected File objects,
saved media references, error/retry behaviour, permissions and label delivery
separation using isolated API substitutes. `npm run check` checks JavaScript syntax;
`npm run build` and `python scripts/preflight.py --working-tree` verify packaging.

The teammate's package reports successful authenticated Administrator uploads for
all three formats from both allowed origins and automated Operator/Viewer checks.
Those live write results are teammate evidence, not a claim that this frontend's
production editors have been exercised with a real account.
