const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const MAX_IMAGE_BYTES = 1048576;
const FORM_TTL_SECONDS = 300;

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function signedForm(upload, issue) {
  const invalidForm = () => issue("The image service returned an invalid upload form. Try again.", "invalid_response", 502);
  if (!isObject(upload) || typeof upload.url !== "string" || !/^https:\/\//i.test(upload.url) ||
      upload.expires_in_seconds !== FORM_TTL_SECONDS || !isObject(upload.fields)) throw invalidForm();
  let url;
  try { url = new URL(upload.url); } catch { throw invalidForm(); }
  if (url.username || url.password || url.hash || /[\s\\]/.test(upload.url)) throw invalidForm();

  const fields = Object.entries(upload.fields);
  if (!upload.fields.key || !upload.fields.policy ||
      fields.some(([key, value]) => !key || key.toLowerCase() === "file" || typeof value !== "string")) throw invalidForm();
  return fields;
}

function validMediaItem(item, metadata) {
  return isObject(item) && Object.entries(metadata).every(([key, value]) => item[key] === value) &&
    Number.isInteger(item.version) && item.version >= 1 &&
    [item.created_at, item.updated_at].every(value => typeof value === "string" && Number.isFinite(Date.parse(value)));
}

export function createImageUploader({ request, issue, newMediaId, fetchUpload, timeoutMs }) {
  // A File is immutable. Keep the ID for retries while the editor holds that
  // selection, including retries after a failed profile or draft association.
  const mediaIds = new WeakMap();

  return async function uploadImage(file, { onProgress, signal } = {}, { assertCurrent, sessionSignal }) {
    assertCurrent();
    if (!(file instanceof Blob) || typeof file.name !== "string" || !IMAGE_TYPES.has(file.type) ||
        file.size < 1 || file.size > MAX_IMAGE_BYTES) {
      throw issue("Choose a PNG, JPEG or WebP image between 1 byte and 1 MiB.", "invalid_image", 400, { image: "Choose a PNG, JPEG or WebP image up to 1 MiB." });
    }
    if (!globalThis.crypto?.subtle) throw issue("Use HTTPS or localhost to verify image uploads.");
    const timeout = new AbortController();
    const uploadSignal = AbortSignal.any([sessionSignal, timeout.signal, ...(signal ? [signal] : [])]);
    function check() {
      assertCurrent();
      if (signal?.aborted) throw issue("Image upload cancelled.", "upload_cancelled", 499);
      if (timeout.signal.aborted) throw issue("The image upload timed out. Retry with the same image.", "upload_timeout", 504);
      if (sessionSignal.aborted) throw issue("The signed-in account changed. Reload this view.", "session_changed", 401);
    }
    function report(message) {
      check();
      try { onProgress?.(message); } catch { /* Progress rendering is independent of the transfer. */ }
      check();
    }

    let timer;
    async function upload() {
      report("Preparing image…");
      if (!mediaIds.has(file)) mediaIds.set(file, newMediaId());
      const mediaId = mediaIds.get(file);
      const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const expectedHash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
      const metadata = { media_id: mediaId, content_type: file.type, size_bytes: file.size };
      const post = (path, body) => request(path, { method: "POST", body, signal: uploadSignal });
      const invalidMedia = () => issue("The image service returned an invalid media record. Try again.", "invalid_response", 502);

      report("Requesting secure upload…");
      const result = await post("/staff-api/media/upload-requests", metadata);
      check();
      let item = result?.item;
      if (!["created", "duplicate"].includes(result?.status) || !validMediaItem(item, metadata)) throw invalidMedia();
      if (item.status === "pending") {
        const fields = signedForm(result.upload, issue);
        const form = new FormData();
        for (const [key, value] of fields) form.append(key, value);
        form.append("file", file);
        report("Uploading image…");
        timer = setTimeout(() => timeout.abort(), timeoutMs);
        try {
          const response = await fetchUpload(result.upload.url, {
            method: "POST", body: form, credentials: "omit", referrerPolicy: "no-referrer", redirect: "error", signal: uploadSignal,
          });
          check();
          if (!response?.ok) throw new Error("Upload rejected");
        } catch {
          check();
          throw issue("The secure upload did not finish. Retry with the same image.", "upload_failed", 502);
        } finally {
          clearTimeout(timer);
        }
        report("Verifying image…");
        item = (await post(`/staff-api/media/${encodeURIComponent(mediaId)}/complete`, {}))?.item;
        check();
      } else {
        if (result.status !== "duplicate" || Object.hasOwn(result, "upload")) throw invalidMedia();
        report("Verifying image…");
      }
      if (!validMediaItem(item, metadata) || item.status !== "ready" || typeof item.sha256 !== "string" ||
          !/^[a-f0-9]{64}$/.test(item.sha256)) throw invalidMedia();
      if (item.sha256 !== expectedHash) {
        throw issue("The verified image does not match the selected file. Choose the image again to start a new upload.", "media_digest_mismatch", 409);
      }
      return { imageMediaId: mediaId };
    }

    check();
    let onAbort;
    const interrupted = new Promise((_, reject) => {
      onAbort = () => { try { check(); } catch (error) { reject(error); } };
      uploadSignal.addEventListener("abort", onAbort, { once: true });
    });
    try {
      const result = await Promise.race([interrupted, upload()]);
      check();
      return result;
    } finally {
      clearTimeout(timer);
      uploadSignal.removeEventListener("abort", onAbort);
    }
  };
}
