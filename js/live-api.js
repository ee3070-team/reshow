// Live staff API adapter. Device observations and receipts are authoritative.
// This module never produces telemetry, scans or acknowledgements.
import { createImageUploader } from "./image-upload.js";

const PREFIX = "/staff-api";
const SNAPSHOT_COLLECTIONS = ["exhibits", "operations", "staff", "reports", "events"];
const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
const uid = (prefix) => {
  if (!globalThis.crypto?.randomUUID) throw issue("Use HTTPS or localhost to generate safe request identifiers.");
  return `${prefix}-${globalThis.crypto.randomUUID()}`;
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const encode = (value) => encodeURIComponent(String(value));
const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
function issue(message, code = "validation", status = 400, fields) {
  return Object.assign(new Error(message), { code, status, ...(fields ? { fields } : {}) });
}
function text(value, field, max, required = true) {
  const result = String(value ?? "").trim();
  if ((required && !result) || result.length > max)
    throw issue(`${field} ${!result ? "is required" : `must be ${max} characters or fewer`}.`, "validation", 400, { [field]: !result ? "Required" : `Maximum ${max} characters` });
  return result;
}
function identifier(value, field = "id") {
  const result = text(value, field, 64);
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(result))
    throw issue(`${field} must contain letters, numbers, underscores or hyphens.`, "validation", 400, { [field]: "Invalid identifier" });
  return result;
}
function number(value, field, min, max, integer = false) {
  const result = typeof value === "string" && value.trim() === "" ? Number.NaN : Number(value);
  if (value == null || !Number.isFinite(result) || result < min || result > max || (integer && !Number.isInteger(result)))
    throw issue(`${field} must be ${integer ? "a whole number " : ""}between ${min} and ${max}.`, "validation", 400, { [field]: `Enter ${min}–${max}` });
  return result;
}
function version(value, allowZero = false) {
  if (!Number.isInteger(value) || value < (allowZero ? 0 : 1))
    throw issue("The version from the opened record is required. Reload the record before editing.", "version_required", 428);
  return String(value);
}
function query(path, values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values || {}))
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  return `${path}${params.size ? `?${params}` : ""}`;
}
const role = (value) => {
  if (!["Administrator", "Operator", "Viewer"].includes(value)) throw issue("Choose a valid website or directory role.");
  return value;
};
const date = (instant) => new Date(Date.parse(instant) + 8 * 3600000).toISOString().slice(0, 10);
const endDate = (instant) => date(new Date(Date.parse(instant) - 1).toISOString());

export function createLiveApi(auth, { uploadFetch = globalThis.fetch, uploadTimeoutMs = 60000 } = {}) {
  if (typeof auth?.request !== "function") throw new TypeError("A staff authentication request adapter is required.");
  const listeners = new Set(), drafts = new Map(), staffRecords = new Map(), media = new Map(), mediaWarnings = new Map();
  const pendingCreates = new Map(), pendingCommands = new Map(), snapshotRequests = new Map();
  const inFlightReads = new Map();
  const minimumRequestIntervalMs = auth.minimumRequestIntervalMs ?? 550;
  let requestTail = Promise.resolve(), nextRequestAt = 0, generation = 0;
  const identity = () => JSON.stringify(auth.getSession?.() || null);
  let sessionIdentity = identity();
  let sessionController = new AbortController();
  let selectedShowcaseId = null, selectedShowcase = null, pollTimer = null, pollBusy = false, pollEpoch = 0;
  let pollReads = null;
  const snapshotPending = new Map();
  let scanEvidence = null;
  const permissionMap = { view: "view", reports: "view", saveReports: "save_reports", operate: "device_commands", editExhibits: "edit_exhibits", staff: "manage_staff", accounts: "manage_accounts", cards: "physical_cards" };
  function can(permission) {
    if (permission === "demo") return false;
    return Boolean(auth.can?.(permissionMap[permission] || permission));
  }
  function requirePermission(permission) {
    if (!auth.getSession?.()) throw issue("Sign in to continue.", "unauthorized", 401);
    if (!can(permission)) throw issue("Your current account does not permit this action.", "forbidden", 403);
  }
  function editContext() {
    const expectedGeneration = generation, expectedIdentity = identity();
    return {
      sessionSignal: sessionController.signal,
      assertCurrent() {
        checkGeneration(expectedGeneration);
        if (identity() !== expectedIdentity) throw issue("The signed-in account changed. Reload this view.", "session_changed", 401);
        requirePermission("editExhibits");
      },
    };
  }
  const upload = createImageUploader({ request, issue, newMediaId: () => uid("media"), fetchUpload: uploadFetch, timeoutMs: uploadTimeoutMs });
  const uploadImage = (file, options) => upload(file, options, editContext());
  // Mutation notifications run independently: a delayed view must not delay an
  // accepted write. allSettled observes subscriber failures; polling awaits it.
  function emit(event) {
    // Only a poll's awaited notification may reuse its reads. A mutation or
    // identity change invalidates them before subscribers reload current state.
    if (event?.type !== "poll") pollReads = null;
    const pending = [];
    for (const listener of listeners) {
      try { pending.push(listener(event)); } catch (error) { console.error("ReShow live subscriber failed", error); }
    }
    return Promise.allSettled(pending);
  }
  function checkGeneration(expectedGeneration) {
    if (generation !== expectedGeneration) throw issue("The signed-in account changed. Reload this view.", "session_changed", 401);
  }
  function sharedPollReads() {
    return pollReads?.generation === generation && pollReads.showcaseId === selectedShowcaseId ? pollReads : null;
  }
  async function requestSlot(expectedGeneration, signal) {
    const turn = requestTail.then(async () => {
      signal?.throwIfAborted();
      const wait = nextRequestAt - Date.now();
      if (wait > 0) await sleep(wait);
      signal?.throwIfAborted();
      if (generation !== expectedGeneration) throw issue("The signed-in account changed. Reload this view.", "session_changed", 401);
      nextRequestAt = Date.now() + minimumRequestIntervalMs;
    });
    requestTail = turn.catch(() => {});
    await turn;
  }
  async function performRequest(path, options, expectedGeneration) {
    try {
      for (let attempt = 0; ; attempt++) {
        await requestSlot(expectedGeneration, options.signal);
        options.signal?.throwIfAborted();
        try {
          const result = await auth.request(path, options);
          if (generation !== expectedGeneration) throw issue("The signed-in account changed. Reload this view.", "session_changed", 401);
          return result;
        } catch (error) {
          if (error.status !== 429 || (options.method && options.method !== "GET") || attempt >= 2) throw error;
          await sleep(1100 * (attempt + 1));
        }
      }
    }
    catch (error) {
      if (options.signal?.aborted) throw error;
      if (error.status === 401) error.message = "Your live session expired or was revoked. Sign in again.";
      else if (error.status === 403) error.message = `Your current account does not permit this action. ${error.message || ""}`.trim();
      else if (error.status === 409 || error.status === 428) error.message = `The record or device request conflicts with current server state. Reload and review before retrying. ${error.message || ""}`.trim();
      else if (error.ambiguous || ((!error.status || error.name === "AbortError") && options.method && options.method !== "GET")) {
        error.ambiguous = true;
        error.code ||= "ambiguous_mutation";
        error.message = "The request outcome is unknown because the connection ended. Refresh server records before retrying; no acknowledgement has been assumed.";
      }
      throw error;
    }
  }
  function request(path, options = {}) {
    if (!path.startsWith(`${PREFIX}/`)) return Promise.reject(issue("Only staff API routes are permitted."));
    const isRead = !options.method || options.method === "GET", expectedGeneration = generation;
    const key = `${expectedGeneration}:${path}`;
    if (isRead && inFlightReads.has(key)) return inFlightReads.get(key);
    const result = performRequest(path, options, expectedGeneration);
    if (!isRead) return result;
    const pending = result.finally(() => { if (inFlightReads.get(key) === pending) inFlightReads.delete(key); });
    inFlightReads.set(key, pending);
    return pending;
  }
  async function collect(path, filters = {}) {
    const items = [], seen = new Set();
    let cursor = null;
    do {
      const page = await request(query(`${PREFIX}${path}`, { ...filters, limit: 100, cursor }));
      if (!Array.isArray(page?.items) || !own(page, "next_cursor")) throw issue("The server returned an invalid paginated response.", "invalid_response", 502);
      items.push(...page.items);
      cursor = page.next_cursor;
      if (cursor != null) {
        if (typeof cursor !== "string" || !cursor || seen.has(cursor)) throw issue("The server repeated or returned an invalid pagination cursor.", "invalid_cursor", 502);
        seen.add(cursor);
      }
    } while (cursor != null); // Empty filtered pages can still have a next cursor.
    return items;
  }
  async function imageUrl(mediaId) {
    if (!mediaId) return "";
    const expectedGeneration = generation;
    const cached = media.get(mediaId);
    if (cached && cached.expiresAt > Date.now()) return cached.url;
    try {
      const result = await request(`${PREFIX}/media/${encode(mediaId)}`);
      checkGeneration(expectedGeneration);
      const url = result.download?.url || "";
      if (url && !/^https:\/\//i.test(url)) throw issue("The server returned an invalid media download URL.", "invalid_response", 502);
      if (!url) throw issue("The private image is not ready.", "media_not_ready", 409);
      mediaWarnings.delete(mediaId);
      media.set(mediaId, { url, expiresAt: Date.now() + Math.max(0, (result.download?.expires_in_seconds || 0) - 30) * 1000 });
      return url;
    } catch (error) {
      checkGeneration(expectedGeneration);
      if (error.status === 401) throw error;
      mediaWarnings.set(mediaId, `Private image unavailable${error.status ? ` (${error.status})` : ""}. The existing media reference is preserved.`);
      media.set(mediaId, { url: "", expiresAt: Date.now() + 30000 });
      return "";
    }
  }
  async function labelContent(content, extra = {}) {
    if (!content) return null;
    return { title: content.title, collection: content.collection, description: content.description, imageMediaId: content.image_media_id ?? null, image: await imageUrl(content.image_media_id), imageWarning: mediaWarnings.get(content.image_media_id) || null, ...extra };
  }
  function imageReference(profile, existingMediaId = null) {
    if (typeof profile.image === "string" && /^data:/i.test(profile.image))
      throw issue("Local image previews cannot be saved to the live API. Keep the existing private image or use a completed media upload.", "media_upload_required", 400, { image: "Private media upload required" });
    const value = own(profile, "imageMediaId") ? profile.imageMediaId : own(profile, "image_media_id") ? profile.image_media_id : existingMediaId;
    return value == null || value === "" ? null : identifier(value, "imageMediaId");
  }
  function wireLabel(content, existingMediaId = null) {
    return { title: text(content?.title, "title", 100), collection: text(content?.collection, "collection", 120, false), description: text(content?.description, "description", 2000), image_media_id: imageReference(content || {}, existingMediaId) };
  }
  async function exhibitRecord(item, showcases = []) {
    return { id: item.exhibit_id, name: item.name, description: item.description, tagId: item.tag_id,
      temperatureMin: item.temperature_min_c, temperatureMax: item.temperature_max_c, brightnessMin: item.brightness_min_percent, brightnessMax: item.brightness_max_percent,
      warningSeconds: item.warning_seconds, collection: item.collection || "", imageMediaId: item.image_media_id ?? null, image: await imageUrl(item.image_media_id), imageWarning: mediaWarnings.get(item.image_media_id) || null, version: item.version,
      showcaseId: showcases.find((s) => s.confirmed_exhibit_id === item.exhibit_id)?.showcase_id || null };
  }
  const staffRecord = (item) => ({ id: item.staff_id, name: item.name, email: item.email, role: item.role, version: item.version, cardId: item.card_id || null, cardEnabled: Boolean(item.card_enabled), cardDeviceId: item.card_device_id || null, cardAcknowledgedAt: item.card_acknowledged_at || null });
  async function commandRecord(item) {
    const payload = clone(item.payload || {});
    if (payload.content) payload.content = await labelContent(payload.content);
    if (own(payload, "exhibit_id")) payload.exhibitId = payload.exhibit_id;
    if (own(payload, "staff_id")) payload.staffId = payload.staff_id;
    if (own(payload, "card_id")) payload.cardId = payload.card_id;
    return { id: item.command_id, type: item.type, targetId: item.type === "card" ? payload.staff_id : ["assignment", "label"].includes(item.type) ? payload.exhibit_id : item.showcase_id,
      status: item.status, requestedAt: item.created_at, acknowledgedAt: item.acknowledged_at || null, payload,
      error: item.failure_reason || null, source: item.acknowledgement_source || null, version: item.version,
      executeBefore: item.execute_before || null, recovery: clone(item.recovery || null), raw: clone(item) };
  }
  async function commands(filters = {}) {
    const shared = sharedPollReads();
    const all = shared && shared.showcase?.device_id === filters.device_id && Object.keys(filters).length === 1
      ? shared.commands : await collect("/commands", filters);
    // Browser display filtering is not an API authorization boundary. Backend
    // per-type redaction is not promised by the published contract.
    return Promise.all(all.filter((item) => item.type !== "card" || can("cards")).map(commandRecord));
  }
  async function showcaseRecord(item) {
    const status = item?.device_status, freshness = item?.freshness;
    return { id: item?.showcase_id || null, name: item?.name || "No showcase", deviceId: item?.device_id || null, exhibition: "Live showcase",
      exhibitId: item?.confirmed_exhibit_id || null, version: item?.version || null,
      online: Boolean(status && freshness?.contact_stale === false), lastReceivedAt: status?.created_at || null, source: status?.source || null,
      recordedAt: status?.recorded_at || null, localControl: status?.local_control ?? null,
      temperature: status?.temperature_c ?? null, brightness: status?.brightness_percent ?? null,
      zone: status?.occupied == null ? null : status.occupied ? "Occupied" : "Clear",
      lightingMode: item?.lighting_mode ?? status?.lighting_mode ?? null, lightingOutput: status?.lighting_output ?? null, latch: status?.latch ?? null,
      freshness: clone(freshness || null), measurementStale: freshness?.measurements_stale ?? null, freshnessNote: freshness?.meaning || (status ? "Freshness was not supplied by the server." : "No device observation has been received."),
      confirmedProfileVersion: item?.confirmed_profile_version ?? null, labelReconciliationRequired: Boolean(item?.label_reconciliation_required),
      label: item?.confirmed_label ? { exhibitId: item.confirmed_label.exhibit_id, content: await labelContent(item.confirmed_label.content), acknowledgedAt: item.confirmed_label.acknowledged_at, draftVersion: item.confirmed_label.draft_version } : null };
  }
  async function chooseShowcase() {
    const expectedGeneration = generation;
    const items = await collect("/showcases");
    checkGeneration(expectedGeneration);
    selectedShowcase = items.find((item) => item.showcase_id === selectedShowcaseId) || items[0] || null;
    selectedShowcaseId = selectedShowcase?.showcase_id || null;
    return { items, selected: selectedShowcase };
  }
  async function currentShowcase() {
    const expectedGeneration = generation;
    const shared = sharedPollReads();
    if (shared) return shared.showcase;
    if (!selectedShowcaseId && !(await chooseShowcase()).selected) return null;
    checkGeneration(expectedGeneration);
    const id = selectedShowcaseId;
    const item = (await request(`${PREFIX}/showcases/${encode(id)}`)).item;
    checkGeneration(expectedGeneration);
    if (id !== selectedShowcaseId) throw issue("The selected showcase changed. Reload this view.", "showcase_changed", 409);
    if (!item || item.showcase_id !== id) throw issue("The server returned an invalid showcase detail.", "invalid_response", 502);
    selectedShowcase = item;
    return item;
  }
  async function requireShowcase() {
    const item = await currentShowcase();
    if (!item) throw issue("No showcase is configured on the server.", "showcase_unavailable", 404);
    return item;
  }
  const activityRecord = (item, showcaseId) => ({ id: item.activity_id, at: item.created_at, showcaseId, exhibitId: item.exhibit_id || null, type: item.type, details: `${item.message || "Server-recorded workflow event"} · Server audit record`, status: item.status, actor: item.actor || "", source: "server" });
  async function loadSnapshot(include) {
    requirePermission("view");
    const expectedGeneration = generation, shared = sharedPollReads();
    const listed = shared ? { items: shared.items, selected: shared.showcase } : await chooseShowcase();
    // The list is a discovery projection. Only exact detail supplies the
    // current observations and confirmed device state for the selected case.
    const selected = shared ? shared.showcase : listed.selected ? await currentShowcase() : null;
    const items = listed.items.map((item) => item.showcase_id === selected?.showcase_id ? selected : item);
    const [exhibits, operations, workflow, staff, reports] = await Promise.all([
      include.has("exhibits") ? collect("/exhibit-profiles").then((records) => Promise.all(records.map((item) => exhibitRecord(item, items)))) : [],
      include.has("operations") && selected ? commands({ device_id: selected.device_id }) : [],
      include.has("events") && selected && can("staff") ? collect("/workflow-activity", { device_id: selected.device_id }) : [],
      include.has("staff") && can("staff") ? listStaff() : [], include.has("reports") ? listReports() : [],
    ]);
    const result = { now: new Date().toISOString(), mode: "live", showcase: await showcaseRecord(selected), showcases: await Promise.all(items.map(showcaseRecord)), exhibits, staff, operations,
      events: workflow.filter((item) => can("cards") || !/card|access/i.test(item.type)).map((item) => activityRecord(item, selected?.showcase_id)),
      reports, assignmentHistory: [], storageWarning: null, limitations: ["Live device evidence is supplied by a producer. The website cannot simulate scans or acknowledge commands.", ...(!can("staff") ? ["Detailed workflow audit records and tag scans are available only to Administrators in the current backend."] : [])] };
    checkGeneration(expectedGeneration);
    return result;
  }
  function getSnapshot({ include = SNAPSHOT_COLLECTIONS } = {}) {
    if (!Array.isArray(include) || include.some((item) => !SNAPSHOT_COLLECTIONS.includes(item)))
      return Promise.reject(issue("Choose valid snapshot collections."));
    // Scope only concurrent work. Completed snapshots never become a cache:
    // subsequent reads must see corrections even when record IDs are unchanged.
    const collections = SNAPSHOT_COLLECTIONS.filter((item) => include.includes(item));
    const key = collections.join(",");
    if (snapshotPending.has(key)) return snapshotPending.get(key);
    const pending = loadSnapshot(new Set(collections)).finally(() => { if (snapshotPending.get(key) === pending) snapshotPending.delete(key); });
    snapshotPending.set(key, pending);
    return pending;
  }
  async function listExhibits() {
    requirePermission("view");
    const [{ items }, records] = await Promise.all([chooseShowcase(), collect("/exhibit-profiles")]);
    return Promise.all(records.map((item) => exhibitRecord(item, items)));
  }
  function createId(key, prefix, supplied) {
    if (supplied) return identifier(supplied);
    if (!pendingCreates.has(key)) pendingCreates.set(key, uid(prefix));
    return pendingCreates.get(key);
  }
  function matchesFields(item, body) {
    return item && Object.entries(body).every(([key, value]) => item[key] === value);
  }
  async function writeAssociation(path, options, readPath, matches, assertCurrent) {
    let result;
    try { result = await request(path, options); }
    catch (error) {
      assertCurrent();
      const unreadableSuccess = error.code === "invalid_response" && error.status >= 200 && error.status < 300;
      if (!error.ambiguous && error.status !== 503 && !unreadableSuccess) throw error;
      // Reconciliation needs a read started after the write, even if an older
      // view read for this record is still in flight.
      try { result = await performRequest(readPath, {}, generation); }
      catch { assertCurrent(); throw error; }
      assertCurrent();
      if (!matches(result?.item)) throw error;
      return result;
    }
    assertCurrent();
    if (result?.status === "duplicate" && !matches(result.item)) {
      throw issue("The exhibit was created earlier and has since changed. Reload it before editing.", "version_conflict", 409);
    }
    return result;
  }
  async function saveExhibit(profile) {
    const { assertCurrent } = editContext();
    assertCurrent();
    const tagId = identifier(text(profile.tagId, "tagId", 64).toUpperCase(), "tagId");
    if (tagId.startsWith("CARD-")) throw issue("Staff card identifiers cannot be exhibit tags.", "validation", 400, { tagId: "Use an exhibit tag" });
    const body = { name: text(profile.name, "name", 120), description: text(profile.description, "description", 2000, false), tag_id: tagId,
      temperature_min_c: number(profile.temperatureMin, "temperatureMin", -40, 125), temperature_max_c: number(profile.temperatureMax, "temperatureMax", -40, 125),
      brightness_min_percent: number(profile.brightnessMin, "brightnessMin", 0, 100), brightness_max_percent: number(profile.brightnessMax, "brightnessMax", 0, 100),
      warning_seconds: number(profile.warningSeconds, "warningSeconds", 1, 3600, true), collection: text(profile.collection, "collection", 120, false), image_media_id: imageReference(profile) };
    if (body.temperature_min_c >= body.temperature_max_c || body.brightness_min_percent >= body.brightness_max_percent)
      throw issue("Minimum thresholds must be below maximum thresholds.");
    if (profile.id && !own(profile, "imageMediaId") && !own(profile, "image_media_id")) delete body.image_media_id;
    const key = `exhibit:${tagId}`;
    const id = profile.id ? identifier(profile.id) : createId(key, "ex");
    const path = `${PREFIX}/exhibit-profiles`, readPath = `${path}/${encode(id)}`;
    const options = profile.id ? { method: "PATCH", headers: { "If-Match": version(profile.version) }, body }
      : { method: "POST", body: { exhibit_id: id, ...body } };
    const nextVersion = profile.id ? profile.version + 1 : 1;
    const result = await writeAssociation(profile.id ? readPath : path, options, readPath,
      item => item?.exhibit_id === id && item.version === nextVersion && matchesFields(item, body), assertCurrent);
    pendingCreates.delete(key); void emit();
    const saved = await exhibitRecord(result.item, selectedShowcase ? [selectedShowcase] : []);
    assertCurrent();
    return saved;
  }
  async function queue(type, payload, requestId) {
    requirePermission(type === "card" ? "cards" : "operate");
    const showcase = await requireShowcase();
    const key = JSON.stringify([showcase.showcase_id, type, payload]);
    const commandId = requestId || pendingCommands.get(key) || uid("cmd");
    pendingCommands.set(key, commandId);
    const result = await request(`${PREFIX}/commands`, { method: "POST", body: { command_id: commandId, showcase_id: showcase.showcase_id, type, payload } });
    pendingCommands.delete(key); void emit();
    return commandRecord(result.item);
  }
  function rememberDraft(exhibitId, draft) {
    const previous = drafts.get(exhibitId);
    // Draft versions only increase. An earlier read may return after a save.
    if (Number.isInteger(draft?.version) && draft.version > (previous?.version ?? 0)) drafts.set(exhibitId, clone(draft));
  }
  async function getLabel(exhibitId) {
    requirePermission("view"); identifier(exhibitId);
    const expectedGeneration = generation;
    let draft;
    try { draft = (await request(`${PREFIX}/label-drafts/${encode(exhibitId)}`)).item; }
    catch (error) { if (error.status !== 404) throw error; draft = null; }
    checkGeneration(expectedGeneration);
    rememberDraft(exhibitId, draft);
    const [showcase, history] = await Promise.all([currentShowcase(), commands({ exhibit_id: exhibitId, type: "label" })]);
    checkGeneration(expectedGeneration);
    draft = drafts.get(exhibitId) || null;
    const normalized = draft ? await labelContent(draft.content, { version: draft.version }) : null;
    const confirmed = showcase?.confirmed_label ? { exhibitId: showcase.confirmed_label.exhibit_id, content: await labelContent(showcase.confirmed_label.content), acknowledgedAt: showcase.confirmed_label.acknowledged_at } : null;
    checkGeneration(expectedGeneration);
    return { draft: normalized, draftVersion: draft?.version || 0, savedAt: draft?.updated_at || null, confirmed, history, operations: history };
  }
  async function saveLabelDraft(exhibitId, content) {
    const { assertCurrent } = editContext();
    assertCurrent(); identifier(exhibitId);
    const previous = drafts.get(exhibitId);
    const expected = content?.version;
    const body = wireLabel(content, previous?.content?.image_media_id);
    const path = `${PREFIX}/label-drafts/${encode(exhibitId)}`;
    const result = await writeAssociation(path, { method: "PUT", headers: { "If-Match": version(expected, true) }, body }, path,
      item => item?.exhibit_id === exhibitId && item.version === expected + 1 && matchesFields(item.content, body), assertCurrent);
    rememberDraft(exhibitId, result.item); void emit();
    const draft = await labelContent(result.item.content, { version: result.item.version });
    assertCurrent();
    return { draft, draftVersion: result.item.version, savedAt: result.item.updated_at };
  }
  async function sendLabel(exhibitId, content) {
    requirePermission("operate");
    const showcase = await requireShowcase();
    if (showcase.confirmed_exhibit_id !== exhibitId) throw issue("Assign this exhibit to the showcase and wait for acknowledgement before sending its label.");
    const previous = drafts.get(exhibitId);
    const body = wireLabel(content, previous?.content?.image_media_id);
    let draftVersion;
    if (previous && Object.keys(body).every((key) => previous.content[key] === body[key])) {
      if (own(content, "version") && content.version !== previous.version) throw issue("This label draft changed since it was opened. Reload before sending.", "version_conflict", 409);
      draftVersion = previous.version;
    } else draftVersion = (await saveLabelDraft(exhibitId, content)).draftVersion;
    return queue("label", { exhibit_id: identifier(exhibitId), draft_version: draftVersion });
  }
  async function listStaff() {
    requirePermission("staff");
    const result = await collect("/staff");
    result.forEach((item) => staffRecords.set(item.staff_id, clone(item)));
    return result.map(staffRecord);
  }
  async function saveStaff(profile) {
    requirePermission("staff");
    const email = text(profile.email, "email", 160).toLowerCase();
    const parts = email.split("@"), domain = parts[1] || "", dot = domain.lastIndexOf(".");
    if (parts.length !== 2 || !parts[0] || /\s/.test(email) || dot <= 0 || dot === domain.length - 1)
      throw issue("Enter a valid email address.", "validation", 400, { email: "Invalid email" });
    const body = { name: text(profile.name, "name", 100), email, role: role(profile.role) }, key = `staff:${email}`;
    const result = profile.id ? await request(`${PREFIX}/staff/${encode(identifier(profile.id))}`, { method: "PATCH", headers: { "If-Match": version(profile.version) }, body })
      : await request(`${PREFIX}/staff`, { method: "POST", body: { staff_id: createId(key, "staff"), ...body } });
    staffRecords.set(result.item.staff_id, clone(result.item)); pendingCreates.delete(key); void emit();
    return staffRecord(result.item);
  }
  function scanAvailability() {
    const available = Boolean(can("staff") && can("cards"));
    return { available, reason: available ? "Waiting reads real uploaded scans. A connected producer must supply a new scan; the website cannot start hardware." : "The backend currently permits only Administrators to read tag scans. Operators can enter an exhibit tag manually." };
  }
  async function waitForScan(type, options = {}) {
    requirePermission(type === "staff_card" ? "cards" : "operate");
    if (!scanAvailability().available) throw issue(scanAvailability().reason, "scan_read_forbidden", 403);
    if (type === "staff_card") scanEvidence = null;
    const showcase = await requireShowcase(), started = Date.now(), startedAt = new Date(started).toISOString();
    const scanAttemptId = uid("scan"), timeout = Math.min(Math.max(options.timeoutMs ?? 30000, 1), 120000);
    options.onWaiting?.({ scanAttemptId, deviceId: showcase.device_id, startedAt });
    while (Date.now() - started < timeout) {
      if (options.signal?.aborted) throw issue("The scan was cancelled.", "scan_cancelled", 499);
      const records = await collect("/tag-scans", { device_id: showcase.device_id, start: startedAt });
      const now = Date.now();
      const matching = records.filter((item) => item.source === "device" && item.scan_type === type && item.device_id === showcase.device_id &&
        Date.parse(item.recorded_at) >= started && Date.parse(item.created_at) >= started && Date.parse(item.recorded_at) <= now && Date.parse(item.created_at) <= now &&
        now - Date.parse(item.recorded_at) <= 120000 && now - Date.parse(item.created_at) <= 120000 && (type !== "staff_card" || item.scan_attempt_id === scanAttemptId))
        .sort((a, b) => Date.parse(b.recorded_at) - Date.parse(a.recorded_at));
      if (matching.length) {
        const item = matching[0];
        if (type === "staff_card") scanEvidence = clone(item);
        return { id: item.tag_id, scanId: item.scan_id, scanAttemptId: item.scan_attempt_id || null, deviceId: item.device_id, recordedAt: item.recorded_at, receivedAt: item.created_at };
      }
      await sleep(Math.min(options.pollMs ?? 1500, Math.max(1, timeout - (Date.now() - started))));
    }
    throw issue(type === "staff_card" ? "No new real card scan matched this registration attempt. The scanning producer must echo the displayed attempt ID; no card was registered." : "No new real exhibit scan was received. Present the tag to a connected scanning producer or enter its ID manually.", "scan_timeout", 408);
  }
  async function registerCard(staffId, cardId) {
    requirePermission("cards"); identifier(staffId);
    cardId = text(cardId, "cardId", 64).toUpperCase();
    if (!/^CARD-[A-Z0-9-]+$/.test(cardId)) throw issue("Choose a staff card identifier beginning CARD-.");
    const evidence = scanEvidence, showcase = await requireShowcase(), now = Date.now();
    if (!evidence || evidence.tag_id !== cardId || evidence.device_id !== showcase.device_id || !evidence.scan_attempt_id ||
      now - Date.parse(evidence.recorded_at) > 120000 || now - Date.parse(evidence.created_at) > 120000)
      throw issue("A fresh real card scan for this registration attempt is required. Scan the card again.", "fresh_scan_required", 409);
    const result = await queue("card", { staff_id: staffId, card_id: cardId, enabled: true, scan_id: evidence.scan_id, scan_attempt_id: evidence.scan_attempt_id });
    scanEvidence = null;
    return result;
  }
  async function setCardAccess(staffId, enabled, options = {}) {
    requirePermission("cards");
    if (typeof enabled !== "boolean") throw issue("Card access must be enabled or disabled.");
    const person = (await request(`${PREFIX}/staff/${encode(identifier(staffId))}`)).item;
    if (own(options, "expectedCardId") && person.card_id !== options.expectedCardId)
      throw issue("The assigned card changed after this confirmation was opened. Reload staff access before requesting another change.", "card_changed", 409);
    if (!person.card_id) throw issue("Register a staff card first.");
    return queue("card", { staff_id: staffId, card_id: person.card_id, enabled });
  }
  async function getAccessLog() {
    requirePermission("staff");
    const showcase = await currentShowcase();
    if (!showcase) return [];
    // The contract records a card, not its owner at the time of the attempt.
    // Current directory assignments cannot establish historical attribution.
    return (await collect("/access-attempts", { device_id: showcase.device_id })).map((item) => ({ id: item.attempt_id, at: item.recorded_at, staffId: null,
      cardId: item.card_id, result: item.decision === "accepted" ? "Accepted" : "Denied", details: item.reason || "Producer access assertion; physical latch motion is not independently verified.", source: item.source }));
  }
  const accountRecord = (item) => ({ id: item.staff_id, staffId: item.staff_id, email: item.invited_email || staffRecords.get(item.staff_id)?.email || "", name: staffRecords.get(item.staff_id)?.name || "", role: item.role,
    status: item.status, version: item.version, delivery: item.delivery || null, providerStatus: item.provider_status || null });
  async function listAccounts() {
    requirePermission("accounts");
    return (await collect("/accounts")).map(accountRecord);
  }
  async function updateAccount(staffId, patch) {
    requirePermission("accounts");
    const body = {};
    if (own(patch, "role")) body.role = role(patch.role);
    if (own(patch, "status")) {
      if (!["active", "disabled"].includes(patch.status)) throw issue("Only active or disabled may be requested.");
      body.status = patch.status;
    }
    if (!Object.keys(body).length) throw issue("Choose a website account role or status change.");
    const result = await request(`${PREFIX}/accounts/${encode(identifier(staffId))}`, { method: "PATCH", headers: { "If-Match": version(patch.version) }, body });
    void emit(); return accountRecord(result.item);
  }
  async function prepareAccountInvitation(staffId, accountRole) {
    requirePermission("accounts");
    if (staffId && typeof staffId === "object") { accountRole = staffId.role; staffId = staffId.staffId || staffId.id; }
    const key = `invite:${identifier(staffId)}`;
    const result = await request(`${PREFIX}/accounts/invitations`, { method: "POST", body: { invitation_id: createId(key, "invite"), staff_id: staffId, role: role(accountRole), delivery: "suppress" } });
    pendingCreates.delete(key); void emit(); return { ...accountRecord(result.item), note: result.note || "Account prepared without sending an invitation email." };
  }
  function filtersFor(raw, showcaseId) {
    return { showcaseId: showcaseId || selectedShowcaseId || "", start: raw.date_selection?.start_date || date(raw.start), end: raw.date_selection?.end_date || endDate(raw.end), exhibitId: raw.filter?.exhibit_id || raw.exhibit_id || "" };
  }
  function normalizeActivity(raw, filters) {
    const showcaseId = filters.showcaseId || selectedShowcaseId || "", summary = raw.summary || {};
    const intervals = (raw.buckets || []).map((bucket, index) => ({ ...clone(bucket), id: `live-bucket-${index}`, start: bucket.start, end: bucket.end, showcaseId, exhibitId: filters.exhibitId || null,
      availability: ["recorded_activity", "confirmed_quiet"].includes(bucket.status) ? "recorded" : bucket.status, events: bucket.proximity_event_count ?? null, occupiedSeconds: bucket.recorded_occupied_seconds ?? null }));
    const warnings = (raw.environmental_warnings || []).map((item, index) => ({ id: `${item.warning_id}-${index}`, sourceId: item.warning_id, at: item.clipped_start || item.start, end: item.clipped_end || item.end, showcaseId, exhibitId: item.exhibit_id,
      type: item.warning_type, status: "warning", details: `${item.message} · ${item.source === "demo" ? "Synthetic producer data" : "Device producer assertion"}`, source: item.source }));
    const events = (raw.proximity_events || []).map((item, index) => ({ id: `${item.proximity_event_id}-${index}`, sourceId: item.proximity_event_id, at: item.clipped_start || item.start, end: item.clipped_end || item.end,
      showcaseId, exhibitId: filters.exhibitId || null, type: "proximity", status: "recorded", details: `${item.event_start_attributed === false ? "Recorded occupancy carried into the selected exhibit interval; no new event start counted." : "Recorded proximity detection-zone event; not a unique visitor."} · ${item.source === "demo" ? "Synthetic producer data" : "Device producer assertion"}`, source: item.source }));
    const denominator = summary.coverage_seconds + summary.missing_seconds;
    return { filters: clone(filters), intervals, events: [...events, ...warnings].sort((a, b) => b.at.localeCompare(a.at)), warnings,
      assignments: (raw.display_history || []).map((item, index) => ({ id: `${item.display_id}-${index}`, sourceId: item.display_id, exhibitId: item.exhibit_id, showcaseId, start: item.clipped_start || item.start, end: item.clipped_end || item.end, source: item.source })),
      summary: { proximityEvents: summary.proximity_event_count ?? null, occupiedSeconds: summary.recorded_occupied_seconds ?? null, warnings: summary.warning_count ?? null,
        missingIntervals: intervals.filter((item) => ["missing", "partial", "unknown_assignment", "ambiguous_assignment"].includes(item.availability)).length,
        recordedIntervals: intervals.filter((item) => item.availability === "recorded").length, expectedIntervals: intervals.filter((item) => !["future", "no_known_display"].includes(item.availability)).length,
        coveragePercent: denominator > 0 ? Math.round(summary.coverage_seconds / denominator * 1000) / 10 : null, coverageSeconds: summary.coverage_seconds ?? null, missingSeconds: summary.missing_seconds ?? null,
        coverageComplete: summary.coverage_complete ?? null, interpretation: summary.totals_interpretation, warningInterpretation: summary.warning_count_interpretation, ...clone(summary) },
      completeness: clone(raw.data_completeness || {}), missingData: clone(raw.missing_data || {}), provenance: clone(raw.provenance || {}), limitations: clone(raw.limitations || []),
      dataQuality: clone(raw.data_quality || {}), evaluation: clone(raw.evaluation || null), notices: [], raw: clone(raw) };
  }
  function validateFilters(filters = {}) {
    const today = date(new Date().toISOString()), result = { showcaseId: filters.showcaseId || selectedShowcaseId || "", start: filters.start || today, end: filters.end || today, exhibitId: filters.exhibitId || "" };
    for (const key of ["start", "end"]) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(result[key]) || !Number.isFinite(Date.parse(`${result[key]}T00:00:00Z`)) || new Date(`${result[key]}T00:00:00Z`).toISOString().slice(0, 10) !== result[key])
        throw issue("Choose valid start and end dates.", "validation", 400, { [key]: "Invalid date" });
    }
    if (result.start > result.end) throw issue("Start date must be on or before end date.", "validation", 400, { end: "Must follow start date" });
    if ((Date.parse(`${result.end}T00:00:00Z`) - Date.parse(`${result.start}T00:00:00Z`)) / 86400000 >= 31) throw issue("Choose a range of at most 31 inclusive calendar days.");
    if (result.exhibitId) identifier(result.exhibitId);
    return result;
  }
  async function getActivity(filters = {}) {
    requirePermission("view");
    const value = validateFilters(filters), showcase = await requireShowcase();
    if (value.showcaseId && value.showcaseId !== showcase.showcase_id) throw issue("Select the intended live showcase before querying its activity.");
    value.showcaseId = showcase.showcase_id;
    const raw = await request(query(`${PREFIX}/activity`, { device_id: showcase.device_id, start_date: value.start, end_date: value.end, timezone: "Asia/Hong_Kong", interval_minutes: 60, exhibit_id: value.exhibitId }));
    const data = normalizeActivity(raw, value);
    if (!can("staff")) {
      data.notices.push("Detailed workflow audit events are available only to Administrators in the current backend. Coverage-aware proximity, display history and environmental warnings remain available.");
      return data;
    }
    const start = raw.evaluation ? raw.evaluation.effective_start : raw.start;
    const end = raw.evaluation ? raw.evaluation.effective_end : raw.end;
    if (start && end && Date.parse(start) < Date.parse(end)) {
      const workflow = await collect("/workflow-activity", { device_id: showcase.device_id, exhibit_id: value.exhibitId, start, end });
      const events = workflow.filter((item) => {
        const at = Date.parse(item.created_at);
        return item.device_id === showcase.device_id && (!value.exhibitId || item.exhibit_id === value.exhibitId) && at >= Date.parse(start) && at < Date.parse(end) &&
          (item.command_id || /(?:^|[_. -])(assignment|label|lighting|card)(?:$|[_. -])/i.test(item.type));
      }).map((item) => {
        const event = activityRecord(item, showcase.showcase_id);
        event.type = ["assignment", "label", "lighting", "card"].find((type) => new RegExp(`(?:^|[_. -])${type}(?:$|[_. -])`, "i").test(item.type)) || "command";
        return event;
      });
      data.events.push(...events);
      data.events.sort((a, b) => b.at.localeCompare(a.at));
    }
    data.notices.push("Workflow audit timestamps describe server requests and receipts. They do not establish physical display intervals or coverage and are not included in immutable analytics snapshots.");
    return data;
  }
  function sectionsFor(sections = ["history", "activity", "environment", "missing"]) {
    if (!Array.isArray(sections) || sections.some((item) => !["history", "activity", "environment", "missing"].includes(item))) throw issue("Choose valid report sections.");
    return [...new Set([...sections, "missing"])];
  }
  async function generateReport(filters = {}, sections) {
    requirePermission("view");
    const data = await getActivity(filters);
    return { id: uid("preview"), saved: false, createdAt: data.evaluation?.captured_at || new Date().toISOString(), title: "Showcase activity report", filters: data.filters, sections: sectionsFor(sections), data,
      exhibitNames: Object.fromEntries((await listExhibits()).map((item) => [item.id, item.name])) };
  }
  function reportMetadata(item) {
    return { id: item.report_id, title: item.title, saved: true, createdAt: item.captured_at, filters: filtersFor(item), sections: clone(item.sections), data: null, exhibitNames: {}, evaluation: clone(item.evaluation || null), version: item.version };
  }
  async function listReports() {
    requirePermission("view");
    return (await collect("/report-snapshots")).map(reportMetadata);
  }
  async function getReport(reportId) {
    requirePermission("view");
    const raw = await request(`${PREFIX}/report-snapshots/${encode(identifier(reportId))}`), data = normalizeActivity(raw.report, filtersFor(raw.report));
    return { id: raw.report_id, saved: true, title: raw.title, createdAt: raw.captured_at, filters: data.filters, sections: clone(raw.sections), data,
      exhibitNames: Object.fromEntries((raw.profile_context?.exhibits || []).map((item) => [item.exhibit_id, item.profile?.name || item.exhibit_id])), profileContext: clone(raw.profile_context), raw: clone(raw) };
  }
  async function saveReport(preview) {
    requirePermission("saveReports");
    if (!preview?.data?.raw) throw issue("Generate a report preview before saving a capture.");
    if (preview.saved) return getReport(preview.id);
    const raw = preview.data.raw, key = preview.id;
    if (!snapshotRequests.has(key)) snapshotRequests.set(key, { report_id: uid("report"), title: text(preview.title || "Showcase activity report", "title", 120), device_id: identifier(raw.device_id), start: raw.start, end: raw.end,
      timezone: "Asia/Hong_Kong", interval_minutes: 60, sections: sectionsFor(preview.sections), ...(preview.filters.exhibitId ? { exhibit_id: identifier(preview.filters.exhibitId) } : {}) });
    const result = await request(`${PREFIX}/report-snapshots`, { method: "POST", body: clone(snapshotRequests.get(key)) });
    // Keep the capture ID even if hydration fails; retry is idempotent.
    const report = await getReport(result.item.report_id); void emit(); return report;
  }
  function stopPolling() {
    clearTimeout(pollTimer); pollTimer = null;
    pollEpoch++; pollReads = null;
  }
  function schedulePoll() {
    if (pollTimer || pollBusy || !listeners.size || !auth.getSession?.() || !can("view")) return;
    pollTimer = setTimeout(poll, 5000);
    pollTimer.unref?.();
  }
  async function poll() {
    pollTimer = null;
    if (pollBusy || !listeners.size || !auth.getSession?.() || !can("view")) return;
    pollBusy = true;
    const expectedGeneration = generation, expectedEpoch = pollEpoch;
    const active = () => generation === expectedGeneration && pollEpoch === expectedEpoch && listeners.size > 0;
    try {
      const { items, selected } = await chooseShowcase();
      if (!active()) return;
      const showcase = selected ? await currentShowcase() : null;
      if (!active()) return;
      const records = showcase ? await collect("/commands", { device_id: showcase.device_id }) : [];
      if (!active()) return;
      pollReads = { generation: expectedGeneration, showcaseId: selectedShowcaseId, items, showcase, commands: records };
      // Analytics and corrected values can change without a new status ID or
      // command version. Every completed cycle invalidates displayed live data.
      await emit({ type: "poll" });
    } catch (error) {
      pollReads = null;
      if (active()) await emit({ type: "poll", error });
    }
    finally {
      pollReads = null;
      pollBusy = false;
      // The delay starts after both reads and subscriber refresh work settle.
      schedulePoll();
    }
  }
  function subscribe(listener) {
    listeners.add(listener);
    schedulePoll();
    return () => { listeners.delete(listener); if (!listeners.size) stopPolling(); };
  }
  auth.onChange?.(() => {
    const nextIdentity = identity();
    if (nextIdentity !== sessionIdentity) {
      sessionIdentity = nextIdentity; generation++;
      const previousController = sessionController;
      sessionController = new AbortController();
      previousController.abort();
      stopPolling();
      media.clear(); mediaWarnings.clear(); drafts.clear(); scanEvidence = null; staffRecords.clear();
      pendingCreates.clear(); pendingCommands.clear(); snapshotRequests.clear(); inFlightReads.clear();
      selectedShowcaseId = null; selectedShowcase = null; snapshotPending.clear();
    }
    void emit();
    schedulePoll();
  });
  const unavailable = async () => { throw issue("Demo simulation is unavailable in live mode.", "live_only", 403); };
  return { getMode: () => "live", getDemoMode: () => "live", can, getSession: auth.getSession, subscribe, getSnapshot,
    listExhibits, uploadImage, saveExhibit, assignExhibit: (exhibitId) => queue("assignment", { exhibit_id: identifier(exhibitId) }),
    getLabel, saveLabelDraft, sendLabel, setLighting: (mode) => { if (!["Auto", "On", "Off"].includes(mode)) return Promise.reject(issue("Choose Auto, On or Off lighting.")); return queue("lighting", { mode }); },
    scanAvailability, scanExhibitTag: (options) => waitForScan("exhibit", options), scanStaffCard: (options) => waitForScan("staff_card", options),
    listStaff, saveStaff, registerCard, setCardAccess, getAccessLog, listAccounts, updateAccount,
    saveAccount: (profile) => updateAccount(profile.staffId || profile.id, profile), prepareAccountInvitation,
    getActivity, generateReport, saveReport, listReports, getReport,
    selectShowcase: async (id) => {
      const nextId = identifier(id);
      stopPolling(); selectedShowcaseId = nextId;
      try { const result = await currentShowcase(); void emit(); return showcaseRecord(result); }
      finally { schedulePoll(); }
    },
    getShowcases: async () => Promise.all((await collect("/showcases")).map(showcaseRecord)),
    setDemoMode: unavailable, resetDemo: unavailable, settlePending: unavailable };
}
