// Read-only access to the seeded AWS fixture through the signed-in staff session.
export const DEMO_CASE = "demo-test-case";
export const DEMO_DEVICE = "demo-test-device";
const EXHIBITS = ["demo-test-clock", "demo-test-vase", "demo-test-camera"];

function cancelled() {
  return new DOMException("The live showcase load was cancelled.", "AbortError");
}

export function waitForRead(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(cancelled()); return; }
    const finish = () => { signal?.removeEventListener("abort", abort); resolve(); };
    const timer = setTimeout(finish, milliseconds);
    const abort = () => { clearTimeout(timer); signal.removeEventListener("abort", abort); reject(cancelled()); };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

export async function loadLiveShowcase(request, { role, signal, current = () => true,
  onProgress = () => {}, pause = waitForRead } = {}) {
  const result = { profiles: [], drafts: [], readings: [], commands: [], activity: [], scans: [], access: [], errors: [] };
  let started = false;
  const guard = () => { if (signal?.aborted || !current()) throw cancelled(); };
  async function read(path, label) {
    guard();
    if (started) await pause(1100, signal);
    started = true;
    guard();
    onProgress(label);
    try { const value = await request(path, { signal }); guard(); return value; }
    catch (error) {
      guard();
      if (error.status !== 429) throw error;
      onProgress("Service busy; retrying " + label.toLowerCase());
      await pause(2500, signal);
      guard();
      const value = await request(path, { signal });
      guard();
      return value;
    }
  }
  function item(value) {
    if (!value?.item || typeof value.item !== "object" || Array.isArray(value.item)) throw new Error("The service returned an invalid record.");
    return value.item;
  }
  async function optional(label, callback) {
    try { return await callback(); }
    catch (error) {
      guard();
      if (error.status === 401 || error.code === "account_unavailable") throw error;
      result.errors.push({ section: label, status: error.status || 0, message: error.status === 403 ? "Your role cannot read this section." :
        error.status === 429 ? "The service is busy. Refresh again in a few seconds." : error.message || "This section could not be loaded." });
      return null;
    }
  }
  async function list(path, label) {
    const value = await read(path, label);
    if (!Array.isArray(value?.items) || value.items.length > 20) throw new Error("The service returned an invalid record list.");
    return { items: value.items, more: Boolean(value.next_cursor) };
  }
  try { result.showcase = item(await read("/showcases/" + DEMO_CASE, "Showcase")); }
  catch (error) { if (error.status === 404) { result.missing = true; return result; } throw error; }
  if (result.showcase.showcase_id !== DEMO_CASE || result.showcase.device_id !== DEMO_DEVICE) throw new Error("The demo showcase has an unexpected device binding.");
  for (const id of EXHIBITS) {
    const profile = await optional("Exhibit " + id, async () => item(await read("/exhibit-profiles/" + id, "Exhibit profiles")));
    if (profile) result.profiles.push(profile);
    const draft = await optional("Label " + id, async () => item(await read("/label-drafts/" + id, "Label drafts")));
    if (draft) result.drafts.push(draft);
  }
  for (const [key, path, label] of [
    ["readings", "/readings?device_id=" + DEMO_DEVICE + "&limit=20", "Latest readings"],
    ["commands", "/commands?device_id=" + DEMO_DEVICE + "&limit=20", "Command history"],
    ["activity", "/workflow-activity?device_id=" + DEMO_DEVICE + "&limit=20", "Operation history"],
  ]) {
    const page = await optional(label, () => list(path, label));
    if (page) { result[key] = page.items; result[key + "More"] = page.more; }
  }
  result.report = await optional("Activity report", () => read("/reports/event?event_id=demo-test-event&interval_minutes=15&timezone=Asia%2FHong_Kong", "Activity report"));
  result.saved = await optional("Saved report", () => read("/report-snapshots/demo-test-report", "Saved report"));
  if (role === "Administrator" || role === "Operator") {
    const page = await optional("Tag scans", () => list("/tag-scans?device_id=" + DEMO_DEVICE + "&limit=20" +
      (role === "Operator" ? "&scan_type=exhibit" : ""), "Tag scans"));
    if (page) { result.scans = page.items; result.scansMore = page.more; }
  }
  if (role === "Administrator") {
    result.cardHolder = await optional("Demo card holder", async () => item(await read("/staff/demo-test-card-holder", "Demo card holder")));
    const page = await optional("Access history", () => list("/access-attempts?device_id=" + DEMO_DEVICE + "&limit=20", "Access history"));
    if (page) { result.access = page.items; result.accessMore = page.more; }
  }
  guard();
  result.loadedAt = new Date().toISOString();
  return result;
}

export function mergeLiveShowcase(previous, latest) {
  if (!previous || previous.missing || latest.missing) return latest;
  const result = { ...latest, profiles: [...latest.profiles], drafts: [...latest.drafts],
    errors: latest.errors.map((error) => ({ ...error })) };
  const sections = { "Latest readings": "readings", "Command history": "commands", "Operation history": "activity",
    "Activity report": "report", "Saved report": "saved", "Tag scans": "scans", "Demo card holder": "cardHolder", "Access history": "access" };
  for (const error of result.errors) {
    // A forbidden response revokes this section's old display as well.
    if (error.status === 403) continue;
    const key = sections[error.section];
    if (key && previous[key] !== null && previous[key] !== undefined) {
      result[key] = previous[key];
      result[key + "More"] = previous[key + "More"];
      error.retained = true;
    }
    for (const [prefix, list] of [["Exhibit ", "profiles"], ["Label ", "drafts"]]) {
      const old = previous[list].find((record) => prefix + record.exhibit_id === error.section);
      if (old) { result[list].push(old); error.retained = true; }
    }
  }
  return result;
}
