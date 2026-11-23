import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createLiveApi } from "../js/live-api.js";

const SIGNED_URL = "https://bucket.example.invalid/upload?test=signature";
const DOWNLOAD_URL = "https://bucket.example.invalid/private-image?test=signature";
const TIME = "2026-11-10T06:00:00.000000Z";
const image = (type = "image/png", bytes = [1, 2, 3]) => new File([new Uint8Array(bytes)], "test-image", { type });
const digest = async file => createHash("sha256").update(new Uint8Array(await file.arrayBuffer())).digest("hex");
const formFields = {
  key: "media/staging/mock-key", policy: "mock-policy", "Content-Type": "image/png",
  "x-amz-signature": "mock-signature", "x-amz-security-token": "mock-temporary-capability",
};

function fixture(file = image(), overrides = {}) {
  const calls = [], transfers = [];
  let session = { id: "alice", role: "Operator" }, onChange, metadata;
  async function reply(path, options) {
    const item = { ...metadata, created_at: TIME, updated_at: TIME };
    if (path.endsWith("/upload-requests")) {
      return { status: "created", item: { ...item, version: 1, status: "pending" },
        upload: { url: SIGNED_URL, fields: { ...formFields, "Content-Type": options.body.content_type }, expires_in_seconds: 300 } };
    }
    if (path.endsWith("/complete")) return { item: { ...item, version: 2, status: "ready", sha256: await digest(file) } };
    if (path.startsWith("/staff-api/media/")) return { download: { url: DOWNLOAD_URL, expires_in_seconds: 300 } };
    throw new Error("Unexpected staff request");
  }
  const api = createLiveApi({
    minimumRequestIntervalMs: overrides.requestInterval ?? 0,
    getSession: () => session,
    can: permission => Boolean(session) && (permission === "view" ||
      (["edit_exhibits", "device_commands"].includes(permission) && ["Operator", "Administrator"].includes(session.role))),
    onChange: callback => { onChange = callback; },
    async request(path, options) {
      calls.push({ path, ...options });
      if (path.endsWith("/upload-requests")) metadata = options.body;
      return overrides.request ? overrides.request(path, options, () => reply(path, options)) : reply(path, options);
    },
  }, {
    uploadTimeoutMs: overrides.timeout,
    async uploadFetch(url, options) {
      transfers.push({ url, ...options });
      return overrides.fetch ? overrides.fetch(url, options) : { ok: true, status: 204 };
    },
  });
  return { api, calls, transfers, setSession(value) { session = value; onChange(); } };
}

for (const type of ["image/png", "image/jpeg", "image/webp"]) {
  test(`stages and verifies ${type} with the signed fields followed by the file`, async () => {
    const file = image(type), { api, calls, transfers } = fixture(file), progress = [];
    const { imageMediaId } = await api.uploadImage(file, { onProgress: message => progress.push(message) });
    assert.match(imageMediaId, /^media-[a-f0-9-]{36}$/);
    assert.deepEqual(calls.map(({ path, method, body }) => ({ path, method, body })), [
      { path: "/staff-api/media/upload-requests", method: "POST", body: { media_id: imageMediaId, content_type: type, size_bytes: 3 } },
      { path: `/staff-api/media/${imageMediaId}/complete`, method: "POST", body: {} },
    ]);
    assert.equal(transfers.length, 1);
    const { url, method, credentials, headers, referrerPolicy, redirect, body } = transfers[0];
    assert.equal(url, SIGNED_URL);
    assert.equal(method, "POST");
    assert.equal(credentials, "omit");
    assert.equal(headers, undefined);
    assert.equal(referrerPolicy, "no-referrer");
    assert.equal(redirect, "error");
    assert.deepEqual([...body.entries()], [...Object.entries({ ...formFields, "Content-Type": type }), ["file", file]]);
    assert.deepEqual(progress, ["Preparing image…", "Requesting secure upload…", "Uploading image…", "Verifying image…"]);
  });
}

test("lost completion response retries the same file as a ready duplicate and checks current permissions", async () => {
  const file = image();
  let ready;
  const { api, calls, transfers, setSession } = fixture(file, {
    async request(path, options, reply) {
      if (path.endsWith("/complete")) {
        ready = (await reply()).item;
        throw new TypeError("Disconnected");
      }
      if (ready) return { status: "duplicate", item: ready };
      return reply();
    },
  });
  await assert.rejects(api.uploadImage(file), { code: "ambiguous_mutation" });
  assert.deepEqual(await api.uploadImage(file), { imageMediaId: calls[0].body.media_id });
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[2].body, calls[0].body);
  assert.equal(transfers.length, 1);
  setSession({ id: "alice", role: "Viewer" });
  await assert.rejects(api.uploadImage(file), { code: "forbidden" });
  assert.equal(calls.length, 3);
});

test("failed S3 transfer retries the same file with a fresh form and a new selection gets a new ID", async () => {
  const file = image(), revisions = new Set();
  let attempts = 0;
  const { api, calls, transfers } = fixture(file, {
    async request(path, options, reply) {
      const result = await reply();
      if (result.upload) {
        result.status = revisions.has(options.body.media_id) ? "duplicate" : "created";
        revisions.add(options.body.media_id);
        result.upload.fields.policy = `policy-${attempts}`;
      }
      return result;
    },
    async fetch() {
      if (++attempts === 1) throw new Error(`Sensitive response for ${SIGNED_URL}`);
      return { ok: true };
    },
  });
  await assert.rejects(api.uploadImage(file), error => error.code === "upload_failed" && !error.message.includes(SIGNED_URL));
  assert.deepEqual(await api.uploadImage(file), { imageMediaId: calls[0].body.media_id });
  assert.deepEqual(calls[1].body, calls[0].body);
  assert.deepEqual(transfers.map(transfer => transfer.body.get("policy")), ["policy-0", "policy-1"]);
  assert.notEqual((await api.uploadImage(image())).imageMediaId, calls[0].body.media_id);
});

test("validates MIME type and byte size before the first staff request", async () => {
  const { api, calls } = fixture();
  for (const file of [null, image("image/gif"), image("image/png", []), image("image/png", new Uint8Array(1048577))]) {
    await assert.rejects(api.uploadImage(file), { code: "invalid_image" });
  }
  assert.equal(calls.length, 0);
  for (const size of [1, 1048576]) {
    const file = image("image/png", new Uint8Array(size));
    assert.ok((await fixture(file).api.uploadImage(file)).imageMediaId);
  }
});

test("rejects malformed upload responses without posting their signed fields", async () => {
  const corruptions = [
    result => { result.status = "unknown"; },
    result => { result.item.media_id = "wrong-id"; },
    result => { result.item.size_bytes = "3"; },
    result => { result.item.status = "ready"; },
    result => { result.upload.url = "http://bucket.example.invalid"; },
    result => { result.upload.url = "https://secret@bucket.example.invalid"; },
    result => { result.upload.fields.file = "another file"; },
    result => { result.upload.fields.policy = 42; },
    result => { result.upload.expires_in_seconds = 0; },
  ];
  for (const corrupt of corruptions) {
    const file = image();
    const { api, transfers } = fixture(file, {
      async request(_path, _options, reply) { const result = await reply(); corrupt(result); return result; },
    });
    await assert.rejects(api.uploadImage(file), { code: "invalid_response" });
    assert.equal(transfers.length, 0);
  }
});

for (const duplicate of [true, false]) {
  test(`rejects a digest mismatch ${duplicate ? "on ready duplicate" : "after completion"}`, async () => {
    const file = image();
    const { api, transfers } = fixture(file, {
      async request(path, _options, reply) {
        const result = await reply();
        if (path.endsWith("/complete")) result.item.sha256 = "0".repeat(64);
        else if (duplicate) return { status: "duplicate", item: { ...result.item, status: "ready", version: 2, sha256: "0".repeat(64) } };
        return result;
      },
    });
    await assert.rejects(api.uploadImage(file), { code: "media_digest_mismatch" });
    assert.equal(transfers.length, duplicate ? 0 : 1);
  });
}

test("S3 timeout aborts the request and never finalizes it", { timeout: 1000 }, async () => {
  const file = image();
  const { api, calls, transfers } = fixture(file, { timeout: 10, fetch: () => new Promise(() => {}) });
  await assert.rejects(api.uploadImage(file), { code: "upload_timeout" });
  assert.equal(transfers[0].signal.aborted, true);
  assert.equal(calls.length, 1);
});

test("cancellation during the staff request cannot post a late signed form", async () => {
  const file = image(), controller = new AbortController();
  const entered = Promise.withResolvers(), release = Promise.withResolvers();
  const { api, calls, transfers } = fixture(file, {
    async request(_path, _options, reply) { entered.resolve(); await release.promise; return reply(); },
  });
  const pending = api.uploadImage(file, { signal: controller.signal });
  await entered.promise;
  controller.abort("external cancellation reason");
  await assert.rejects(pending, { code: "upload_cancelled" });
  assert.equal(calls[0].signal.aborted, true);
  release.resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(transfers.length, 0);
});

test("cancellation while completion waits for staff pacing prevents the write", async () => {
  const file = image(), controller = new AbortController();
  const { api, calls, transfers } = fixture(file, { requestInterval: 80 });
  const pending = api.uploadImage(file, { signal: controller.signal, onProgress(message) {
    if (message === "Verifying image…") setTimeout(() => controller.abort(), 5);
  } });
  await assert.rejects(pending, { code: "upload_cancelled" });
  await new Promise(resolve => setTimeout(resolve, 85));
  assert.equal(transfers.length, 1);
  assert.equal(calls.length, 1);
});

test("a progress rendering error does not interrupt the transfer", async () => {
  const file = image(), { api, calls } = fixture(file);
  const result = await api.uploadImage(file, { onProgress() { throw new Error("The progress view is unavailable"); } });
  assert.equal(result.imageMediaId, calls[0].body.media_id);
  assert.equal(calls.length, 2);
});

for (const next of [null, { id: "bob", role: "Administrator" }, { id: "alice", role: "Viewer" }]) {
  test(`session change to ${next ? `${next.id}/${next.role}` : "signed out"} aborts S3 and prevents completion`, async () => {
    const file = image(), entered = Promise.withResolvers(), release = Promise.withResolvers();
    const { api, calls, transfers, setSession } = fixture(file, {
      async fetch() { entered.resolve(); await release.promise; return { ok: true }; },
    });
    const pending = api.uploadImage(file);
    await entered.promise;
    setSession(next);
    await assert.rejects(pending, { code: "session_changed" });
    assert.equal(transfers[0].signal.aborted, true);
    release.resolve();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, 1);
  });
}

test("reconciles a lost exhibit write only for the matching record, fields, and next version", async () => {
  const profile = { id: "ex-one", version: 4, imageMediaId: "media-new", name: "Bowl", description: "Glazed", tagId: "TAG-1",
    temperatureMin: 15, temperatureMax: 25, brightnessMin: 0, brightnessMax: 70, warningSeconds: 20, collection: "Ceramics" };
  for (const diff of [{}, { exhibit_id: "ex-other" }, { image_media_id: "media-old" }, { name: "Updated elsewhere" }, { version: 6 }]) {
    let stored;
    const { api, calls } = fixture(undefined, {
      request(path, options, reply) {
        if (path.startsWith("/staff-api/media/")) return reply();
        if (options.method === "PATCH") {
          stored = { exhibit_id: profile.id, version: 5, ...options.body, ...diff };
          throw new TypeError("Lost response");
        }
        return { item: stored };
      },
    });
    if (Object.keys(diff).length) await assert.rejects(api.saveExhibit(profile), { code: "ambiguous_mutation" });
    else assert.equal((await api.saveExhibit(profile)).imageMediaId, "media-new");
    assert.deepEqual(calls.slice(0, 2).map(({ path, method }) => [path, method]), [
      ["/staff-api/exhibit-profiles/ex-one", "PATCH"], ["/staff-api/exhibit-profiles/ex-one", undefined],
    ]);
    assert.equal(calls[0].headers["If-Match"], "4");
  }
});

test("a first draft PUT with a lost response reconciles version one without another PUT", async () => {
  let stored;
  const { api, calls } = fixture(undefined, {
    request(path, options, reply) {
      if (path.startsWith("/staff-api/media/")) return reply();
      if (options.method === "PUT") {
        stored = { exhibit_id: "ex-one", content: options.body, version: 1, updated_at: TIME };
        throw new TypeError("Lost response");
      }
      return { item: stored };
    },
  });
  const saved = await api.saveLabelDraft("ex-one", { version: 0, title: "Bowl", description: "Glazed", imageMediaId: "media-new" });
  assert.equal(saved.draft.imageMediaId, "media-new");
  assert.equal(saved.draftVersion, 1);
  assert.equal(calls[0].headers["If-Match"], "0");
  assert.deepEqual(calls.map(call => call.method), ["PUT", undefined, undefined]);
});

test("a known version conflict remains a conflict even when content might match", async () => {
  const { api, calls } = fixture(undefined, {
    request() { throw Object.assign(new Error("Version conflict"), { status: 409, code: "version_conflict" }); },
  });
  await assert.rejects(api.saveLabelDraft("ex-one", { version: 0, title: "Bowl", description: "Glazed", imageMediaId: "media-new" }), { code: "version_conflict" });
  assert.equal(calls.length, 1);
});

for (const initialVersion of [0, 1]) {
  test(`a delayed ${initialVersion ? "older draft" : "not found"} read cannot regress the saved image or trigger another PUT`, async () => {
    const exhibitId = "ex-one", entered = Promise.withResolvers(), release = Promise.withResolvers();
    const showcase = { showcase_id: "showcase-one", device_id: "device-one", confirmed_exhibit_id: exhibitId };
    let stored;
    const { api, calls } = fixture(undefined, {
      async request(path, options, reply) {
        if (path.startsWith("/staff-api/media/")) return reply();
        if (path.endsWith(`/label-drafts/${exhibitId}`)) {
          if (options.method === "PUT") {
            assert.equal(stored, undefined, "sending the saved draft must not repeat its PUT");
            stored = { exhibit_id: exhibitId, content: options.body, version: initialVersion + 1, updated_at: TIME };
            return { item: stored };
          }
          entered.resolve();
          await release.promise;
          if (!initialVersion) throw Object.assign(new Error("No draft"), { status: 404 });
          return { item: { exhibit_id: exhibitId, version: initialVersion, content: { ...stored.content, image_media_id: "media-old" } } };
        }
        if (path.startsWith("/staff-api/showcases?")) return { items: [showcase], next_cursor: null };
        if (path.endsWith(`/showcases/${showcase.showcase_id}`)) return { item: showcase };
        if (path.startsWith("/staff-api/commands?")) return { items: [], next_cursor: null };
        if (path.endsWith("/commands")) return { item: { ...options.body, status: "pending" } };
        throw new Error("Unexpected staff request");
      },
    });
    const reading = api.getLabel(exhibitId);
    await entered.promise;
    const saved = await api.saveLabelDraft(exhibitId, { version: initialVersion, title: "Bowl", description: "Glazed", imageMediaId: "media-new" });
    release.resolve();
    const current = await reading;
    assert.equal(current.draft.imageMediaId, "media-new");
    assert.equal(current.draftVersion, initialVersion + 1);
    const command = await api.sendLabel(exhibitId, saved.draft);
    assert.equal(command.payload.draft_version, initialVersion + 1);
    assert.equal(calls.filter(call => call.method === "PUT").length, 1);
  });
}

test("ambiguous draft association reads back independently of an earlier pending draft read", { timeout: 1000 }, async () => {
  const exhibitId = "ex-one", entered = Promise.withResolvers(), release = Promise.withResolvers();
  let reads = 0, stored;
  const { api } = fixture(undefined, {
    async request(path, options, reply) {
      if (path.startsWith("/staff-api/media/")) return reply();
      if (path.endsWith(`/label-drafts/${exhibitId}`)) {
        if (options.method === "PUT") {
          stored = { exhibit_id: exhibitId, content: options.body, version: 1, updated_at: TIME };
          throw new TypeError("Lost response");
        }
        if (++reads === 1) {
          entered.resolve();
          await release.promise;
          throw Object.assign(new Error("No draft at start of read"), { status: 404 });
        }
        return { item: stored };
      }
      if (path.startsWith("/staff-api/showcases?") || path.startsWith("/staff-api/commands?")) return { items: [], next_cursor: null };
      throw new Error("Unexpected staff request");
    },
  });
  const reading = api.getLabel(exhibitId);
  await entered.promise;
  try {
    const saved = await api.saveLabelDraft(exhibitId, { version: 0, title: "Bowl", description: "Glazed", imageMediaId: "media-new" });
    assert.equal(saved.draft.imageMediaId, "media-new");
    assert.equal(reads, 2);
  } finally {
    release.resolve();
  }
  assert.equal((await reading).draftVersion, 1);
});
