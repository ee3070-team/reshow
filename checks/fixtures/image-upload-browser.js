import { render as renderExhibits } from "../../js/pages/exhibits.js";
import { render as renderLabels } from "../../js/pages/labels.js";

const container = document.querySelector("#page-content");
const output = document.querySelector("[data-browser-result]");
const select = (selector) => document.querySelector(selector);
const profileForm = "dialog[open] .exhibit-profile-form";
const profileFile = `${profileForm} [name=imageFile]`;
const profileSave = `${profileForm} [type=submit]`;
const labelFile = ".label-editor [data-image]";
const firstId = "ex-pot";
const secondId = "ex-clock";
const now = "2026-11-03T05:00:00.000Z";
const storedImage = "assets/mineral.svg";
const replacementImage = "assets/ballon.jpg";

function check(condition, message) {
  if (!condition) throw new Error(message);
}

const tick = () => new Promise(resolve => setTimeout(resolve, 10));

async function waitFor(condition, message) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (condition()) return;
    await tick();
  }
  throw new Error(message);
}

function click(selector) {
  const button = select(selector);
  check(button && !button.disabled, `Expected an enabled control at ${selector}`);
  button.click();
}

function input(selector, value, event = "input") {
  const field = select(selector);
  check(field && !field.disabled, `Expected an enabled field at ${selector}`);
  field.value = value;
  field.dispatchEvent(new Event(event, { bubbles: true }));
}

function sampleImage(name, color) {
  const canvas = document.createElement("canvas");
  canvas.width = 144;
  canvas.height = 90;
  const context = canvas.getContext("2d");
  context.fillStyle = color;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#f6e2b9";
  context.beginPath();
  context.arc(72, 44, 27, 0, Math.PI * 2);
  context.fill();
  const bytes = Uint8Array.from(atob(canvas.toDataURL("image/png").split(",")[1]), character => character.charCodeAt(0));
  return new File([bytes], name, { type: "image/png" });
}

function chooseFile(selector, file) {
  const field = select(selector);
  check(field && !field.disabled, `Expected an enabled file picker at ${selector}`);
  const transfer = new DataTransfer();
  transfer.items.add(file);
  field.files = transfer.files;
  field.dispatchEvent(new Event("change", { bubbles: true }));
}

function imageDisplayed(selector, source) {
  const image = select(selector);
  return image && !image.hidden && image.complete && image.naturalWidth > 0 && source.test(image.getAttribute("src"));
}

function visibleError(selector, text) {
  const alert = select(selector);
  return alert && !alert.hidden && alert.textContent.includes(text);
}

function backend({ mode = "live", operator = true } = {}) {
  const exhibit = {
    id: firstId, version: 3, name: "Porcelain vase", tagId: "EX-01",
    description: "Glazed ceramic with a floral design.", collection: "Ceramics",
    temperatureMin: 20, temperatureMax: 26, brightnessMin: 30, brightnessMax: 70, warningSeconds: 120,
    imageMediaId: "media-profile-original", image: storedImage, showcaseId: "case-01",
  };
  const exhibits = [exhibit, {
    ...exhibit, id: secondId, name: "Mechanical clock", tagId: "EX-02", showcaseId: null,
    imageMediaId: "media-clock", image: "assets/clock.svg",
  }];
  const draft = {
    title: "Vase draft", collection: exhibit.collection, description: exhibit.description,
    imageMediaId: "media-draft-original", image: storedImage, version: 5,
  };
  const label = { exhibitId: firstId, acknowledgedAt: now, content: {
    ...draft, title: "Acknowledged vase", imageMediaId: "media-acknowledged", image: "assets/vase.svg",
  } };
  const drafts = new Map([[firstId, draft]]);
  const operations = [];
  const calls = [];
  const listeners = new Set();
  const media = new Map(exhibits.map(record => [record.imageMediaId, record.image]));
  const uploadedFiles = new WeakMap();
  media.set(draft.imageMediaId, draft.image);
  const fake = {
    calls, exhibits, drafts, dirty: false, busy: false, holdUpload: false,
    failNextUpload: false, failNextProfileSave: false, failNextSend: false, pendingUpload: null,
  };
  const notify = () => listeners.forEach(listener => { void listener(); });
  const materialize = (record, previousImage) => mode === "live"
    ? { ...record, image: Object.hasOwn(record, "imageMediaId") ? media.get(record.imageMediaId) || "" : previousImage || "" }
    : record;
  const logWrite = (name, record, exhibitId) => {
    calls.push({ name, record: structuredClone(record), exhibitId });
    if (mode === "live") {
      check(!JSON.stringify(record).match(/(?:data|blob):/), `${name} received a browser-only image URL`);
      check(!Object.values(record).some(value => value instanceof File), `${name} received binary bytes`);
    }
  };
  fake.api = {
    getMode: () => mode,
    can: () => operator,
    getSnapshot: async () => structuredClone({
      exhibits, operations,
      showcase: { id: "case-01", name: "Case 01", exhibitId: firstId, label, online: true,
        localControl: true, confirmedProfileVersion: 3, lastReceivedAt: now },
    }),
    getLabel: async (id) => structuredClone({
      draft: drafts.get(id) ?? null, draftVersion: drafts.get(id)?.version ?? 0,
      savedAt: drafts.has(id) ? now : null, confirmed: label,
      history: operations.filter(operation => operation.targetId === id),
    }),
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async uploadImage(file, { onProgress, signal }) {
      check(file instanceof File, "Upload must receive a browser File");
      if (uploadedFiles.has(file)) return { imageMediaId: uploadedFiles.get(file) };
      const imageMediaId = `media-upload-${calls.length + 1}`;
      calls.push({ name: "upload", file, signal });
      onProgress("Uploading image…");
      if (fake.holdUpload) {
        await new Promise((resolve, reject) => {
          const onAbort = () => finish(signal.reason);
          const finish = (error) => {
            fake.pendingUpload = null;
            signal.removeEventListener("abort", onAbort);
            if (error) reject(error);
            else resolve();
          };
          signal.addEventListener("abort", onAbort, { once: true });
          fake.pendingUpload = finish;
        });
      }
      if (fake.failNextUpload) {
        fake.failNextUpload = false;
        throw new Error("Simulated image upload failure");
      }
      onProgress("Verifying image…");
      media.set(imageMediaId, replacementImage);
      uploadedFiles.set(file, imageMediaId);
      return { imageMediaId };
    },
    async saveExhibit(profile) {
      logWrite("profile", profile, profile.id);
      if (fake.failNextProfileSave) {
        fake.failNextProfileSave = false;
        throw new Error("Simulated profile write failure");
      }
      const index = exhibits.findIndex(item => item.id === profile.id);
      const previous = exhibits[index];
      const saved = materialize({ ...previous, ...profile, id: profile.id || "ex-new", version: (previous?.version ?? 0) + 1 }, previous?.image);
      if (index === -1) exhibits.push(saved);
      else exhibits[index] = saved;
      notify();
      return structuredClone(saved);
    },
    async saveLabelDraft(id, content) {
      logWrite("draft", content, id);
      const previous = drafts.get(id);
      if (mode === "live") check(content.version === (previous?.version ?? 0), "Draft write must use the current version");
      const saved = materialize({ ...content, version: (previous?.version ?? 0) + 1 }, previous?.image);
      drafts.set(id, saved);
      notify();
      return { draft: structuredClone(saved), draftVersion: saved.version, savedAt: now };
    },
    async sendLabel(id, content) {
      logWrite("send", content, id);
      if (fake.failNextSend) {
        fake.failNextSend = false;
        throw new Error("Simulated command failure");
      }
      check(id === firstId, "Only the assigned exhibit can send");
      const saved = drafts.get(id);
      check(content.version === saved?.version && content.imageMediaId === saved?.imageMediaId,
        "Send must reference the saved draft version and image");
      const operation = { type: "label", targetId: id, status: "pending", requestedAt: now, payload: { content: structuredClone(saved) } };
      operations.push(operation);
      notify();
      return operation;
    },
  };
  return fake;
}

let dispose = () => {};

async function render(page, fake, params = {}) {
  dispose();
  container.replaceChildren();
  select("#toast-region").replaceChildren();
  const renderer = page === "exhibits" ? renderExhibits : renderLabels;
  dispose = await renderer(container, {
    api: fake.api, params,
    setDirty(value) { fake.dirty = value; },
    setBusy(value) { fake.busy = value; },
    navigate() {},
  });
}

function expectCalls(fake, ...names) {
  const actual = fake.calls.map(call => call.name).join(", ");
  check(actual === names.join(", "), `Expected calls [${names.join(", ")}]; received [${actual}]`);
}

async function liveProfile() {
  const fake = backend();
  await render("exhibits", fake);
  click("[data-edit]");
  await waitFor(() => imageDisplayed(`${profileForm} [data-local-preview]`, /mineral\.svg/), "Editor starts with the saved profile image");
  const first = sampleImage("first.png", "#305589");
  const replacement = sampleImage("profile-replacement.png", "#3e8077");
  chooseFile(profileFile, first);
  const initialPreview = select(`${profileForm} [data-local-preview]`).src;
  chooseFile(profileFile, replacement);
  await waitFor(() => imageDisplayed(`${profileForm} [data-local-preview]`, /^blob:/) &&
    select(`${profileForm} [data-local-preview]`).src !== initialPreview, "The latest profile selection replaces the first preview");
  check(fake.dirty, "An image-only profile edit is dirty");
  expectCalls(fake);

  fake.holdUpload = true;
  click(profileSave);
  await waitFor(() => fake.pendingUpload, "Saving the profile should start an upload");
  check(select(profileSave).disabled && select(profileFile).disabled, "The profile Save and image picker are locked during upload");
  check(/Uploading image/.test(select(`${profileForm} [data-image-status]`).textContent), "Profile editor shows upload progress");
  select(profileSave).click();
  expectCalls(fake, "upload");
  check(fake.calls[0].file === replacement, "Only the latest selected profile File is sent");

  fake.pendingUpload(new Error("Simulated image upload failure"));
  fake.holdUpload = false;
  await waitFor(() => visibleError(`${profileForm} [role=alert]`, "Simulated image upload failure"), "Upload failure is visible in the profile editor");
  check(fake.dirty && imageDisplayed(`${profileForm} [data-local-preview]`, /^blob:/), "A failed upload keeps the profile file and preview");
  check(!select(profileSave).disabled && !select(profileFile).disabled, "Profile controls are usable after failure");
  input(`${profileForm} [name=description]`, "Edited profile description");
  fake.failNextProfileSave = true;
  click(profileSave);
  await waitFor(() => visibleError(`${profileForm} [role=alert]`, "Simulated profile write failure"), "Profile write failure is visible after upload completes");
  expectCalls(fake, "upload", "upload", "profile");
  check(fake.calls[1].file === replacement, "Upload retry uses the same browser File");
  check(select(`${profileForm} [name=description]`).value === "Edited profile description", "Profile text stays in the editor after a write failure");

  click(profileSave);
  await waitFor(() => !select(profileForm) && imageDisplayed(".exhibit-main-image", /ballon\.jpg/), "A successful save shows the reloaded profile image");
  expectCalls(fake, "upload", "upload", "profile", "profile");
  check(fake.calls[2].record.imageMediaId === "media-upload-2" &&
    fake.calls[3].record.imageMediaId === "media-upload-2", "Profile write retry reuses completed media");
  check(!fake.dirty, "A successful profile save clears the dirty flag");

  click("[data-edit]");
  click(`${profileForm} [data-remove-image]`);
  check(fake.dirty, "Removing a saved profile image marks the editor dirty");
  click(profileSave);
  await waitFor(() => !select(profileForm) && imageDisplayed(".exhibit-main-image", /vase\.svg/), "Removing a profile image restores the default artwork");
  expectCalls(fake, "upload", "upload", "profile", "profile", "profile");
  check(fake.calls.at(-1).record.imageMediaId === null, "Removing a profile image saves a null media reference");
}

async function liveLabelDraftAndSend() {
  const fake = backend();
  await render("labels", fake);
  const replacement = sampleImage("label-replacement.png", "#415b9d");
  chooseFile(labelFile, replacement);
  await waitFor(() => imageDisplayed("[data-preview-image]", /^blob:/), "A live replacement shows in the label preview");
  check(fake.dirty && select("[data-editor-image]").src === select("[data-preview-image]").src, "Image-only label changes are dirty and appear in both previews");
  const preview = select("[data-preview-image]").src;
  chooseFile(labelFile, new File(["bad"], "invalid.svg", { type: "image/svg+xml" }));
  await waitFor(() => visibleError("[data-form-error]", "PNG"), "Invalid image selection is visibly rejected");
  check(select("[data-preview-image]").src === preview, "Invalid replacement keeps the selected label image");

  input("#label-exhibit", secondId, "change");
  click("dialog[open] [data-cancel]");
  await waitFor(() => !select("dialog"), "The discard confirmation closes");
  check(select("#label-exhibit").value === firstId, "Canceling discard restores the original exhibit selection");
  check(fake.dirty && select("[data-preview-image]").src === preview, "Canceling discard keeps the label image edit");
  expectCalls(fake);

  input(".label-editor [name=title]", "Saved vase draft");
  fake.holdUpload = true;
  click("[data-save]");
  await waitFor(() => fake.pendingUpload, "Saving the label should upload its replacement image");
  check(select("[data-save]").disabled && select("[data-send]").disabled &&
    select(labelFile).disabled && select("#label-exhibit").disabled, "Label upload locks writes and the exhibit and image pickers");
  check(/Uploading image/.test(select("[data-image-status]").textContent), "Label editor shows upload progress");
  fake.pendingUpload(new Error("Simulated image upload failure"));
  fake.holdUpload = false;
  await waitFor(() => visibleError("[data-form-error]", "Simulated image upload failure"), "Upload failure is visible in the label editor");
  check(select(".label-editor [name=title]").value === "Saved vase draft" && select("[data-preview-image]").src === preview,
    "Failed label upload preserves title and selected image");
  expectCalls(fake, "upload");

  click("[data-save]");
  await waitFor(() => !fake.dirty && imageDisplayed("[data-preview-image]", /ballon\.jpg/) && !select("[data-save]").disabled,
    "Saving the label reloads the draft image and clears dirty state");
  expectCalls(fake, "upload", "upload", "draft");
  check(fake.calls[1].file === replacement && fake.calls[2].record.imageMediaId === "media-upload-2", "Label retry saves the uploaded media ID");
  check(/Draft saved/.test(select("[data-draft-status]").textContent), "Successful draft save has a visible saved status");
  check(select("[data-device-label]").textContent.includes("Acknowledged vase") &&
    select("[data-device-label] img").getAttribute("src") === "assets/vase.svg", "Saving the draft preserves the acknowledged image and title");

  fake.failNextSend = true;
  click("[data-send]");
  click("dialog[open] [data-confirm]");
  await waitFor(() => visibleError("[data-form-error]", "Simulated command failure"), "Failed delivery request is shown in the label editor");
  expectCalls(fake, "upload", "upload", "draft", "send");
  check(!fake.dirty, "A failed command leaves the saved label draft clean");
  click("[data-send]");
  click("dialog[open] [data-confirm]");
  await waitFor(() => /pending/i.test(select("[data-draft-status]").textContent), "Successful delivery request is visibly pending");
  expectCalls(fake, "upload", "upload", "draft", "send", "send");
  check(select("[data-send]").disabled && select("[data-device-label]").textContent.includes("Acknowledged vase"), "Pending delivery leaves the acknowledged label on the showcase");
}

async function liveLabelRemovalAndDiscard() {
  const fake = backend();
  await render("labels", fake);
  chooseFile(labelFile, sampleImage("discarded.png", "#804278"));
  input("#label-exhibit", secondId, "change");
  click("dialog[open] [data-confirm]");
  await waitFor(() => select("[data-preview-title]").textContent === "Mechanical clock", "Confirmed discard opens the second exhibit");
  check(!fake.dirty && select("[data-preview-image]").getAttribute("src") === "assets/clock.svg", "Discard leaves the second exhibit's image intact");
  input("#label-exhibit", firstId, "change");
  await waitFor(() => select("[data-preview-title]").textContent === "Vase draft", "The first exhibit reopens with its saved draft");
  check(select("[data-preview-image]").getAttribute("src") === storedImage, "Discarded image never returns to the draft preview");

  click("[data-remove-image]");
  check(fake.dirty && select("[data-preview-image]").hidden, "Removing a label image hides the preview and marks the draft dirty");
  click("[data-send]");
  click("dialog[open] [data-confirm]");
  await waitFor(() => /pending/i.test(select("[data-draft-status]").textContent), "Sending image removal reaches pending delivery");
  expectCalls(fake, "draft", "send");
  check(fake.calls.every(call => call.record.imageMediaId === null), "Dirty send saves and sends the explicit null media reference");
  check(fake.calls[1].record.version === fake.calls[0].record.version + 1, "Dirty send uses the saved draft version");
}

async function liveLabelUploadAndDisposal() {
  const fake = backend();
  await render("labels", fake);
  chooseFile(labelFile, sampleImage("abort.png", "#375e3f"));
  fake.holdUpload = true;
  click("[data-send]");
  click("dialog[open] [data-confirm]");
  await waitFor(() => fake.pendingUpload, "Sending a dirty label starts its image upload");
  await render("exhibits", fake);
  await tick();
  expectCalls(fake, "upload");
  check(fake.calls[0].signal.aborted && !fake.pendingUpload, "Disposing the label editor aborts the active upload");
  check(!fake.dirty && select(".exhibit-main-image"), "Disposed upload cannot modify the newly rendered page");

  await render("labels", fake);
  fake.holdUpload = false;
  chooseFile(labelFile, sampleImage("send.png", "#416699"));
  click("[data-send]");
  click("dialog[open] [data-confirm]");
  await waitFor(() => /pending/i.test(select("[data-draft-status]").textContent), "Sending a replacement image reaches pending delivery");
  expectCalls(fake, "upload", "upload", "draft", "send");
  check(fake.calls.slice(2).every(call => call.record.imageMediaId === "media-upload-2"),
    "Direct send saves a draft using the completed replacement, then sends it");
}

async function viewerHasReadOnlyImages() {
  const fake = backend({ operator: false });
  await render("exhibits", fake);
  check(!select("[data-edit]") && !select(".exhibit-add"), "Viewer has no profile edit or create control");
  check(select(".exhibit-main-image").getAttribute("src") === storedImage, "Viewer still sees the saved exhibit image");
  await render("labels", fake);
  check(select(".label-editor [name=title]").readOnly, "Viewer title field is read-only");
  check([labelFile, "[data-remove-image]", "[data-save]", "[data-send]"].every(selector =>
    !select(selector)?.getClientRects().length), "Viewer has no visible label image or write controls");
  check(select("[data-preview-image]").getAttribute("src") === storedImage, "Viewer still sees the saved draft image");
  expectCalls(fake);
}

async function demoUsesLocalImages() {
  const fake = backend({ mode: "demo" });
  await render("exhibits", fake);
  click("[data-edit]");
  chooseFile(profileFile, sampleImage("demo-profile.png", "#365d7f"));
  await waitFor(() => imageDisplayed(`${profileForm} [data-local-preview]`, /^blob:/), "Demo exhibit previews its local file");
  click(profileSave);
  await waitFor(() => !select(profileForm) && imageDisplayed(".exhibit-main-image", /^data:image\/png/), "Demo exhibit saves a local image");

  await render("labels", fake);
  chooseFile(labelFile, sampleImage("demo-label.png", "#995b55"));
  await waitFor(() => imageDisplayed("[data-preview-image]", /^data:image\/png/), "Demo label previews its local file");
  click("[data-save]");
  await waitFor(() => !fake.dirty && /Draft saved/.test(select("[data-draft-status]").textContent), "Demo label saves a local image");
  await render("labels", fake);
  await waitFor(() => imageDisplayed("[data-preview-image]", /^data:image\/png/), "Demo label reloads its saved local image");
  expectCalls(fake, "profile", "draft");
}

async function runTests() {
  const failures = [];
  window.addEventListener("error", event => failures.push(event.message));
  window.addEventListener("unhandledrejection", event => failures.push(String(event.reason)));
  const scenarios = [
    ["Live profile retries and removal", liveProfile],
    ["Live label upload, draft save, and command retry", liveLabelDraftAndSend],
    ["Live label discard and dirty send", liveLabelRemovalAndDiscard],
    ["Live label upload abort and direct send", liveLabelUploadAndDisposal],
    ["Viewer image access", viewerHasReadOnlyImages],
    ["Demo local images", demoUsesLocalImages],
  ];
  const results = [];
  for (const [name, run] of scenarios) {
    try {
      await run();
      results.push(`PASS ${name}`);
    } catch (error) {
      const dialog = select("dialog h2")?.textContent;
      const status = select("[data-draft-status]")?.textContent;
      const alert = select("[role=alert]:not([hidden])")?.textContent;
      const detail = [dialog, status, alert].filter(Boolean).join(" · ");
      failures.push(`${name} - ${error.message}${detail ? ` (${detail})` : ""}`);
    }
  }
  dispose();
  output.textContent = [...results, ...failures.map(error => `FAIL ${error}`)].join("\n");
  output.style.whiteSpace = "pre-wrap";
  output.dataset.browserResult = failures.length ? "failed" : "passed";
}

async function showFixture(query) {
  const fake = backend({ operator: !query.has("viewer"), mode: query.has("demo") ? "demo" : "live" });
  let page = query.get("page") === "labels" ? "labels" : "exhibits";
  const show = async (next) => {
    page = next;
    await render(page, fake);
    output.textContent = "Local fixture ready";
  };
  const chooseSample = () => {
    if (page === "exhibits" && !select(profileFile)) click("[data-edit]");
    chooseFile(page === "exhibits" ? profileFile : labelFile, sampleImage("sample.png", "#3e8077"));
  };
  await show(page);
  select("[data-fixture-controls]").hidden = query.has("viewer");
  document.querySelectorAll("[data-show-page]").forEach(button =>
    button.addEventListener("click", () => { if (!fake.busy) void show(button.dataset.showPage); }));
  select("[data-sample-image]").onclick = chooseSample;
  select("[data-fail-upload]").onclick = () => {
    fake.failNextUpload = true;
    output.textContent = "The next simulated upload will fail";
  };
  const state = query.get("state");
  if (state === "editing" && page === "exhibits") click("[data-edit]");
  if (["selected", "uploading", "failed"].includes(state)) chooseSample();
  if (state === "uploading" || state === "failed") {
    fake.holdUpload = state === "uploading";
    fake.failNextUpload = state === "failed";
    click(page === "exhibits" ? profileSave : "[data-save]");
  }
}

const query = new URLSearchParams(location.search);
(query.has("test") ? runTests() : showFixture(query)).catch(error => {
  output.textContent = error.stack || String(error);
  output.dataset.browserResult = "failed";
}).finally(() => {
  if (!query.has("test")) return;
  const result = new URL(location.href);
  result.searchParams.set("complete", "");
  return fetch(result, { method: "POST" });
});
