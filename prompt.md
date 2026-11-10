# ReShow backend task: finish and verify image uploads

Please complete the backend work needed for the ReShow website to upload exhibit and electronic-label images. Inspect the existing implementation, fix any gaps, test the complete workflow, and provide a precise frontend integration handoff. Continue through implementation and verification rather than stopping after a plan.

## Background

We previously postponed image-upload integration while completing login and the six dashboard tabs. The reviewed API documentation contained an incorrect finalization address: the staff upload instructions referenced `/api/media/{media_id}/complete`, although staff applications should use `/staff-api/media/{media_id}/complete`.

This inconsistency is also present in our locally saved OpenAPI 0.4.1 snapshot from 4 November 2026. It is a historical finding, not proof that the current deployed backend is broken. Check the current source and deployed contract before making changes.

The current frontend supports local image previews and reading existing private images. It deliberately does not upload selected files. Existing `image_media_id` references survive text edits; new text-only records can use null image references. Completing this backend task will enable a separate frontend upload implementation.

## Read and inspect first

- Read the applicable `AGENT.md` / `AGENTS.md` in your backend workspace and inspect current source, tests, infrastructure configuration, and Git status. Preserve unrelated work.
- Compare the deployed documentation and OpenAPI with the source:
  - https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/docs
  - https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/openapi.json
  - https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/auth/config
- If the frontend checkout is available, consult `README.md`, `project-materials/BACKEND_HANDOFF.md`, `js/live-api.js`, and the image controls in `js/pages/exhibits.js` and `js/pages/labels.js`.
- Older `BACKEND_ASSET_UPLOAD.md` material includes proposed GLB/model storage changes. This task concerns PNG/JPEG/WebP images only; retain the existing `image_media_id` design.

## Required upload workflow

Verify and implement the existing staff workflow below. Preserve compatible routes and response shapes; document any necessary contract change explicitly.

1. An authorized staff user requests an upload through `POST /staff-api/media/upload-requests`, supplying `media_id`, `content_type`, and `size_bytes`.
2. The backend returns media metadata and a short-lived signed S3 upload form.
3. The browser sends all returned form fields and then the binary file directly to the returned S3 HTTPS URL. The file is the last multipart field. No API bearer token is sent to S3, and the browser generates the multipart Content-Type boundary.
4. After S3 accepts the file, the browser calls `POST /staff-api/media/{media_id}/complete` with `{}` and its staff access token.
5. The backend validates the uploaded object and marks the media ready only after successful finalization.
6. The browser associates the ready `media_id` with the intended exhibit profile or label draft through the existing management API, preserving optimistic concurrency requirements such as `If-Match`.
7. `GET /staff-api/media/{media_id}` returns authorized metadata and, when ready, a temporary download URL. The frontend stores the stable media ID on the record and obtains fresh URLs when needed.

Correct obsolete staff-upload examples in OpenAPI descriptions, generated documentation, README files, and frontend handoff notes. Keep intentionally deprecated legacy-route documentation clear; do not restore shared-token access to retired management routes.

## Backend behaviour

- Retain the documented PNG, JPEG, and WebP support and the 1–1,048,576-byte limit unless an agreed current contract supersedes it. State the exact limits in the handoff.
- Validate declared type, actual byte count, object Content-Type, and file signature. Reject empty, oversized, unsupported, missing, or mismatched uploads with documented errors.
- Keep file bytes in private S3 storage and metadata in the database. Do not store base64 image data or expiring download URLs in exhibit or label records.
- Preserve immutable finalized objects: an outstanding staging upload form must not allow a ready image to be replaced. A different image revision gets a new media ID.
- Preserve safe retries: identical upload requests and repeated completion must behave predictably; conflicting metadata must be rejected. Explain recovery after expired upload forms, interrupted uploads, and ambiguous network failures. A ready upload retry must not require uploading bytes again.
- Prevent profile or label association with nonexistent or pending media. Apply existing access boundaries when associating and reading media; do not invent a new ownership model that breaks shared exhibit workflows.
- Preserve the current image until replacement upload, finalization, and record update succeed. A stale record version must not overwrite another user's edit. Preserve null/omitted-field semantics for optional images and document how intentional removal works.
- Do not delete old images still referenced by exhibits, label drafts, pending commands, acknowledged labels, or saved snapshots. Document the existing staging/orphan retention policy and any gap requiring follow-up.
- Saving a label image or draft must not mark the physical display updated. Delivery continues through the existing command and producer-acknowledgement workflow.

## Authentication and browser access

- Verify Administrator and Operator upload/finalization permissions and Viewer read-only media access against the intended existing role policy. Enforce permissions on the backend, including disabled accounts and role changes.
- Reject missing, invalid, or expired staff credentials and prevent legacy/shared producer credentials from gaining staff media-management access.
- Derive audit identity from the validated session. Do not trust a client-supplied actor.
- Verify API and S3 CORS separately for the configured frontend origins. Confirm the current allowlist; expected development/deployed origins include `http://localhost:8000` and `https://dkbznu9f5nh9n.cloudfront.net`.
- Confirm browser upload, finalization, record association, and image readback work with the required methods and headers, including Authorization and If-Match where applicable. Keep storage private and use narrowly scoped CORS settings.
- Keep passwords, access tokens, signed form fields, and signed download URLs out of logs, screenshots, and deliverables. Use normal provider sign-in for interactive checks.

## Verification

Run focused automated tests and, where the existing environment authorization allows it, real integration tests using clearly named synthetic fixtures. Verify:

1. PNG, JPEG, and WebP each complete request → S3 upload → finalization → association → download/readback successfully; verify returned bytes or their hash.
2. The browser flow succeeds from the configured development and deployed origins, or clearly record which origin remains untested. API-only tests do not establish browser CORS success.
3. Administrator and Operator can perform allowed writes; Viewer can read permitted images but cannot upload, finalize, or modify associations.
4. Invalid credentials, disabled users, role changes, and prohibited legacy credentials cannot bypass access checks.
5. Size boundaries, wrong signatures/types, missing objects, pending/unknown references, expired forms, retries, and duplicate/conflicting media IDs behave as documented.
6. Finalized images cannot be changed through a reused staging form, and private objects cannot be read anonymously.
7. Replacing an image works; failed uploads, failed finalization, and stale-version updates preserve the old association. Existing text-only workflows still work.
8. Exhibit and label-draft image associations remain independent unless an explicit existing workflow links them. Saving an image does not fabricate device acknowledgement.
9. Documentation examples use the correct staff routes and match the implemented request/response schemas.

Use isolated test records and clean up only fixtures created for these checks, respecting retained references. Follow existing deployment authorization. If deployment needs separate approval, finish and test the changes, then present the concrete deployment diff and verification plan. Report source changes and deployed behaviour separately.

## Deliverables

- Implemented backend fixes, focused tests, and corrected documentation.
- A short explanation of the actual cause found: documentation only, backend behaviour, CORS, permissions, or a combination.
- A frontend integration guide containing exact routes, request/response examples, authentication, allowed origins, supported formats/size, signed-form handling, expiry/retry rules, media states, association/version rules, and error codes.
- A small browser JavaScript example covering the full upload and readback sequence using placeholders for credentials and signed values. Include how to handle an already-ready duplicate request.
- Test results stating which checks were automated, which used real authenticated API/browser sessions, and which remain unverified.
- Deployment status and API version, plus a clear statement of whether the backend is ready for frontend integration and what frontend work remains.
- A .md file to mark all the work and results

Keep this task focused on backend image-upload readiness. Do not claim the website's preview-only controls now upload images until the separate frontend integration has been implemented and tested.
