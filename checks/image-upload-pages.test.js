import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// Render the real label page and drive its handlers without network or storage.
const ui = `
export const esc = value => String(value ?? "");
export const icon = () => "";
export const formatTime = value => value;
export const formatDate = value => value;
export const badge = value => value;
export const toast = () => {};
export const confirmAction = (...args) => globalThis.confirmLabelAction(...args);
export const fileToDataUrl = () => { throw new Error("Live previews should use object URLs"); };
export const statusStrip = () => "";
`;
const moduleUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const source = await readFile(new URL("../js/pages/labels.js", import.meta.url), "utf8");
const { render } = await import(moduleUrl(source.replace('"../ui.js"', JSON.stringify(moduleUrl(ui)))));

class Element {
  nodes = new Map();
  handlers = new Map();
  attributes = new Map();
  innerHTML = "";
  querySelector(selector) {
    if (!this.nodes.has(selector)) this.nodes.set(selector, new Element());
    return this.nodes.get(selector);
  }
  querySelectorAll() { return Object.values(this.elements || {}); }
  addEventListener(event, handler) { this.handlers.set(event, handler); }
  fire(event) { return this.handlers.get(event)?.({ target: this, preventDefault() {} }); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  insertAdjacentHTML(_position, html) { this.innerHTML += html; }
  reportValidity() { return true; }
}

const png = (name, size = 1) => new File([new Uint8Array(size)], name, { type: "image/png" });
const original = {
  title: "Vase",
  collection: "Museum",
  description: "Ceramic",
  imageMediaId: "existing-image",
  image: "https://private.example/original",
  version: 3,
};
const exhibits = [
  { id: "exhibit-1", name: "Vase", tagId: "EX-1" },
  { id: "exhibit-2", name: "Cup", tagId: "EX-2", image: "https://private.example/cup" },
];
async function fixture(t, { canOperate = true } = {}) {
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  globalThis.confirmLabelAction = async () => true;
  const container = new Element();
  const node = container.querySelector.bind(container);
  const form = node("form");
  form.elements = { title: new Element(), collection: new Element(), description: new Element() };
  const state = { draft: { ...original }, calls: [], dirty: false, busy: false, reads: 0 };
  const api = {
    getMode: () => "live",
    can: () => canOperate,
    getSnapshot: async () => ({
      showcase: { id: "case-1", exhibitId: "exhibit-1", online: true },
      exhibits,
      operations: [],
    }),
    getLabel: async (id) => {
      state.reads++;
      return { draft: id === "exhibit-1" ? { ...state.draft } : null, history: [] };
    },
    uploadImage: async (file, options) => {
      state.calls.push({ method: "upload", file, options });
      if (state.upload) await state.upload(options);
      return { imageMediaId: "new-image" };
    },
    saveLabelDraft: async (id, content) => {
      state.calls.push({ method: "save", id, content });
      if (state.saveError) throw state.saveError;
      const image = content.imageMediaId ? `https://private.example/${content.imageMediaId}` : "";
      state.draft = { ...content, image, version: content.version + 1 };
      return { draft: { ...state.draft } };
    },
    sendLabel: async (id, content) => {
      state.calls.push({ method: "send", id, content });
      if (state.sendError) throw state.sendError;
    },
    subscribe: (refresh) => {
      state.poll = refresh;
      return () => {};
    },
  };
  const dispose = await render(container, {
    api,
    setDirty: (dirty) => { state.dirty = dirty; },
    setBusy: (busy) => { state.busy = busy; },
  });
  t.after(() => {
    dispose();
    delete globalThis.window;
    delete globalThis.confirmLabelAction;
  });
  const input = node("[data-image]");
  const choose = (file) => {
    input.files = [file];
    return input.fire("change");
  };
  const preview = () => node("[data-preview-image]").attributes.get("src");
  const save = () => node("[data-save]").fire("click");
  const send = () => form.fire("submit");
  return { state, node, form, choose, preview, save, send, dispose, container };
}

test("image-only label edit survives polling, validates size, and saves the latest file", async (t) => {
  const f = await fixture(t);
  await f.choose(png("empty.png", 0));
  assert.match(f.node("[data-form-error]").textContent, /non-empty/);
  assert.equal(f.preview(), original.image);
  assert.equal(f.state.dirty, false);

  const first = png("first.png");
  const latest = png("latest.png", 1024 * 1024);
  await f.choose(first);
  const firstPreview = f.preview();
  await f.choose(latest);
  const latestPreview = f.preview();
  assert.match(latestPreview, /^blob:/);
  assert.notEqual(latestPreview, firstPreview);
  await f.choose(png("too-large.png", 1024 * 1024 + 1));
  assert.equal(f.preview(), latestPreview);
  assert.equal(f.state.dirty, true);
  await f.state.poll();
  assert.equal(f.preview(), latestPreview);
  assert.deepEqual(f.state.calls, []);

  await f.save();
  const [upload, save] = f.state.calls;
  assert.equal(upload.file, latest);
  assert.equal(save.content.imageMediaId, "new-image");
  assert.equal(save.content.image, original.image);
  assert.equal(f.state.dirty, false);
  assert.equal(f.preview(), "https://private.example/new-image");
});

test("failed upload and draft conflict keep edits and the original association for retry", async (t) => {
  const f = await fixture(t);
  const selected = png("replacement.png");
  await f.choose(selected);
  const preview = f.preview();
  f.form.elements.title.value = "New title";
  f.form.elements.title.fire("input");
  f.state.upload = async () => { throw new Error("Upload interrupted"); };
  await f.save();
  assert.deepEqual(f.state.calls.map(({ method }) => method), ["upload"]);
  assert.match(f.node("[data-form-error]").textContent, /Upload interrupted/);
  assert.equal(f.preview(), preview);
  assert.equal(f.state.draft.imageMediaId, original.imageMediaId);
  assert.equal(f.state.dirty, true);

  f.state.upload = null;
  f.state.saveError = Object.assign(new Error("stale version"), { status: 409 });
  await f.save();
  assert.equal(f.state.calls[1].file, selected);
  assert.equal(f.state.calls[2].content.title, "New title");
  assert.equal(f.state.calls[2].content.version, original.version);
  assert.match(f.node("[data-form-error]").textContent, /edits are preserved/);
  assert.equal(f.preview(), preview);
  assert.equal(f.state.draft.imageMediaId, original.imageMediaId);
  assert.equal(f.state.dirty, true);
});

test("removing a saved label image is reversible and saves an explicit null reference", async (t) => {
  const f = await fixture(t);
  const remove = f.node("[data-remove-image]");
  remove.fire("click");
  assert.equal(f.preview(), undefined);
  assert.equal(f.state.dirty, true);
  remove.fire("click");
  assert.equal(f.preview(), original.image);
  assert.equal(f.state.dirty, false);
  remove.fire("click");
  await f.save();
  assert.deepEqual(f.state.calls.map(({ method }) => method), ["save"]);
  assert.equal(f.state.calls[0].content.imageMediaId, null);
  assert.equal(f.state.calls[0].content.image, "");
  assert.equal(f.state.dirty, false);
  assert.equal(remove.hidden, true);
});

test("upload conflict can ask for a fresh selection without a record conflict message", async (t) => {
  const f = await fixture(t);
  await f.choose(png("replacement.png"));
  f.state.upload = async () => {
    throw Object.assign(new Error("Choose the image again to start a new upload."), {
      status: 409,
      code: "media_digest_mismatch",
    });
  };
  await f.save();
  assert.match(f.node("[data-form-error]").textContent, /Choose the image again/);
  assert.deepEqual(f.state.calls.map(({ method }) => method), ["upload"]);
});

test("send retries only the device request when the uploaded draft was saved", async (t) => {
  const f = await fixture(t);
  await f.choose(png("replacement.png"));
  f.state.sendError = new Error("Device request interrupted");
  await f.send();
  assert.deepEqual(f.state.calls.map(({ method }) => method), ["upload", "save", "send"]);
  assert.equal(f.state.calls[2].content.version, original.version + 1);
  assert.equal(f.state.calls[2].content.imageMediaId, "new-image");
  assert.equal(f.state.dirty, false);
  assert.match(f.node("[data-draft-status]").innerHTML, /Draft saved/);
  assert.match(f.node("[data-form-error]").textContent, /Device request interrupted/);

  f.state.sendError = null;
  await f.send();
  assert.deepEqual(f.state.calls.map(({ method }) => method), ["upload", "save", "send", "send"]);
  assert.deepEqual(f.state.calls[2].content, f.state.calls[3].content);
});

test("upload freezes label controls and disposal aborts the pending association", async (t) => {
  const f = await fixture(t);
  await f.choose(png("selected.png"));
  let finish;
  f.state.upload = ({ onProgress }) => {
    onProgress("Checking image…");
    return new Promise((resolve) => { finish = resolve; });
  };
  const saving = f.save();
  assert.equal(f.state.busy, true);
  assert.match(f.node("[data-image-status]").textContent, /Checking image/);
  for (const selector of ["[data-image]", "[data-save]", "[data-send]", "[data-remove-image]", "#label-exhibit"])
    assert.equal(f.node(selector).disabled, true);
  assert.equal(f.form.elements.title.disabled, true);
  const reads = f.state.reads;
  await f.state.poll();
  assert.equal(f.state.reads, reads);
  await f.save();
  const select = f.node("#label-exhibit");
  select.value = "exhibit-2";
  await select.fire("change");
  assert.equal(select.value, "exhibit-1");
  f.dispose();
  assert.equal(f.state.calls[0].options.signal.aborted, true);
  finish();
  await saving;
  assert.deepEqual(f.state.calls.map(({ method }) => method), ["upload"]);
});

test("changes made during confirmation require a fresh send confirmation", async (t) => {
  const f = await fixture(t);
  let confirm;
  globalThis.confirmLabelAction = () => new Promise((resolve) => { confirm = resolve; });
  const sending = f.send();
  await f.choose(png("new.png"));
  confirm(true);
  await sending;
  assert.deepEqual(f.state.calls, []);
  assert.match(f.node("[data-form-error]").textContent, /changed while confirming/);
  assert.equal(f.state.dirty, true);
});

test("switching exhibits preserves the selected image until discard is confirmed", async (t) => {
  const f = await fixture(t);
  await f.choose(png("replacement.png"));
  const previousPreview = f.preview();
  const select = f.node("#label-exhibit");
  globalThis.confirmLabelAction = async () => false;
  select.value = "exhibit-2";
  await select.fire("change");
  assert.equal(select.value, "exhibit-1");
  assert.equal(f.preview(), previousPreview);
  assert.equal(f.state.dirty, true);

  globalThis.confirmLabelAction = async () => true;
  select.value = "exhibit-2";
  await select.fire("change");
  assert.equal(f.preview(), "https://private.example/cup");
  assert.equal(f.state.dirty, false);
  assert.deepEqual(f.state.calls, []);
});

test("viewer cannot upload, save, send, or remove a label image", async (t) => {
  const f = await fixture(t, { canOperate: false });
  await f.choose(png("new.png"));
  await f.save();
  await f.send();
  assert.deepEqual(f.state.calls, []);
  assert.equal(f.preview(), original.image);
  assert.equal(f.form.querySelector(".image-upload").hidden, true);
  assert.doesNotMatch(f.container.innerHTML, /data-remove-image/);
});
