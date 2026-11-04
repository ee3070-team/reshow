import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { loadLiveShowcase, mergeLiveShowcase, waitForRead } from "../js/live-showcase.js";
import { createLiveRefresh } from "../js/live-refresh.js";

function fixture(path) {
  if (path === "/showcases/demo-test-case") return { item: { showcase_id: "demo-test-case", device_id: "demo-test-device" } };
  if (path.startsWith("/exhibit-profiles/") || path.startsWith("/label-drafts/") || path.startsWith("/staff/")) return { item: {} };
  if (path.startsWith("/reports/event?")) return { summary: { recorded_occupied_seconds: 600 } };
  if (path.startsWith("/report-snapshots/")) return { report_id: "demo-test-report", report: {} };
  return { items: [], next_cursor: null };
}
const immediate = async () => {};

test("live fixture reads are sequential, bounded and use only the staff request boundary", async () => {
  const calls = [], pauses = [];
  let active = 0, mostActive = 0;
  const result = await loadLiveShowcase(async (path) => {
    calls.push(path);
    active++; mostActive = Math.max(mostActive, active);
    await new Promise(setImmediate);
    active--;
    return fixture(path);
  }, { role: "Administrator", pause: async (delay) => pauses.push(delay) });
  assert.equal(mostActive, 1);
  assert.equal(calls.length, 15);
  assert.equal(pauses.length, calls.length - 1);
  assert.ok(pauses.every((delay) => delay === 1100));
  assert.ok(calls.every((path) => path.startsWith("/") && !path.startsWith("/api")));
  assert.ok(calls.filter((path) => path.includes("?") && !path.startsWith("/reports/")).every((path) => path.includes("limit=20")));
  assert.equal(result.profiles.length, 3);
  assert.equal(result.drafts.length, 3);
  assert.equal(result.errors.length, 0);
});

test("Viewer avoids card and scan endpoints; Operator requests only exhibit scans", async () => {
  for (const role of ["Viewer", "Operator"]) {
    const calls = [];
    await loadLiveShowcase(async (path) => { calls.push(path); return fixture(path); }, { role, pause: immediate });
    assert.ok(!calls.some((path) => path.startsWith("/staff/") || path.startsWith("/access-attempts")));
    const scans = calls.filter((path) => path.startsWith("/tag-scans"));
    assert.equal(scans.length, role === "Operator" ? 1 : 0);
    if (scans.length) assert.ok(scans[0].includes("scan_type=exhibit"));
  }
});

test("missing fixture stops without fetching or inventing linked data", async () => {
  const calls = [];
  const result = await loadLiveShowcase(async (path) => {
    calls.push(path); throw Object.assign(new Error("Not found"), { status: 404 });
  }, { role: "Administrator", pause: immediate });
  assert.equal(result.missing, true);
  assert.deepEqual(calls, ["/showcases/demo-test-case"]);
  assert.deepEqual(result.profiles, []);
});

test("a foreign device binding fails before any linked request", async () => {
  const calls = [];
  await assert.rejects(loadLiveShowcase(async (path) => {
    calls.push(path); return { item: { showcase_id: "demo-test-case", device_id: "other-device" } };
  }, { pause: immediate }), /unexpected device binding/);
  assert.equal(calls.length, 1);
});

test("rate limiting retries once and keeps partial failures explicit", async () => {
  let attempts = 0;
  const result = await loadLiveShowcase(async (path) => {
    if (path.startsWith("/readings")) { attempts++; throw Object.assign(new Error("Busy"), { status: 429 }); }
    return fixture(path);
  }, { role: "Viewer", pause: immediate });
  assert.equal(attempts, 2);
  assert.deepEqual(result.readings, []);
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /service is busy/i);
  assert.equal(result.report.summary.recorded_occupied_seconds, 600);
});

test("session invalidation or navigation stops subsequent reads", async () => {
  const controller = new AbortController(), calls = [];
  await assert.rejects(loadLiveShowcase(async (path) => {
    calls.push(path); controller.abort(); return fixture(path);
  }, { role: "Administrator", signal: controller.signal, pause: immediate }), { name: "AbortError" });
  assert.equal(calls.length, 1);
  let current = true;
  await assert.rejects(loadLiveShowcase(async (path) => {
    current = false; return fixture(path);
  }, { current: () => current, pause: immediate }), { name: "AbortError" });
});

test("an expired session stops the whole load and never becomes partial fixture data", async () => {
  const calls = [];
  await assert.rejects(loadLiveShowcase(async (path) => {
    calls.push(path);
    if (calls.length === 2) throw Object.assign(new Error("Expired"), { status: 401 });
    return fixture(path);
  }, { role: "Administrator", pause: immediate }), /Expired/);
  assert.equal(calls.length, 2);
});

test("paced waits cancel without leaving another scheduled request", async () => {
  const controller = new AbortController();
  const pending = waitForRead(10000, controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
});

function fakeClock() {
  const timers = new Map();
  let id = 0;
  return { timers, setTimer(callback, delay) { const key = ++id; timers.set(key, { callback, delay }); return key; },
    clearTimer(key) { timers.delete(key); }, fireNext() {
      const [key, timer] = timers.entries().next().value;
      timers.delete(key);
      timer.callback();
    } };
}
const settle = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

async function view({ user = null, loader } = {}) {
  const nodes = new Map(["[data-message]", "[data-showcase]", "[data-refresh]", "[data-sign-in]", "[data-auto]", "[data-auto-control]", "[data-auto-status]"].map((key) => [key,
    { innerHTML: "", textContent: "", hidden: false, disabled: false, replaceChildren() { this.innerHTML = ""; } }]));
  const container = { innerHTML: "", querySelector: (key) => nodes.get(key), replaceChildren() {} };
  let listener, state = { status: user ? "authenticated" : "signed_out", user, message: "Sign in to continue." };
  const calls = [], login = [];
  const session = { snapshot: () => state, subscribe: (callback) => { listener = callback; return () => { listener = null; }; },
    request: (path, options) => { calls.push(path); return fixture(path, options); }, startSignIn: async (hash) => login.push(hash) };
  const clock = fakeClock(), events = new Map();
  const document = { hidden: false, addEventListener: (name, callback) => events.set(name, callback),
    removeEventListener: (name) => events.delete(name) };
  globalThis.liveShowcaseTest = { session, loader: loader || loadLiveShowcase, mergeLiveShowcase, createLiveRefresh };
  const original = await readFile("js/pages/live-demo.js", "utf8");
  const source = original.replace('import { staffSession } from "../auth-session.js";', 'const staffSession = globalThis.liveShowcaseTest.session;')
    .replace('import { loadLiveShowcase, mergeLiveShowcase } from "../live-showcase.js";', 'const loadLiveShowcase = globalThis.liveShowcaseTest.loader; const mergeLiveShowcase = globalThis.liveShowcaseTest.mergeLiveShowcase;')
    .replace('import { createLiveRefresh } from "../live-refresh.js";', 'const createLiveRefresh = globalThis.liveShowcaseTest.createLiveRefresh;')
    .replace('import { esc } from "../ui.js";', 'const esc = (text) => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\\\"", "&quot;");');
  const module = await import("data:text/javascript;base64," + Buffer.from(source + "\n//# sourceURL=live-showcase-view-" + crypto.randomUUID() + ".js").toString("base64"));
  delete globalThis.liveShowcaseTest;
  const stop = await module.render(container, { document, refreshRuntime: clock });
  return { nodes, calls, login, module, stop, clock, events, change(next) { state = next; listener?.(state); },
    setHidden(hidden) { document.hidden = hidden; events.get("visibilitychange")?.(); }, settle };
}

test("signed-out view makes no API request and returns sign-in to the live tab", async () => {
  const screen = await view();
  assert.deepEqual(screen.calls, []);
  assert.equal(screen.nodes.get("[data-refresh]").hidden, true);
  await screen.nodes.get("[data-sign-in]").onclick();
  assert.deepEqual(screen.login, ["#live-demo"]);
  screen.stop();
});

test("sign-out removes displayed live records and ignores a late response", async () => {
  let release;
  const user = { issuer: "test", subject: "test", role: "Viewer", account_version: 1 };
  const screen = await view({ user, loader: async () => new Promise((resolve) => { release = resolve; }) });
  screen.change({ status: "signed_out", user: null, message: "Signed out" });
  release({ missing: true });
  await screen.settle();
  assert.equal(screen.nodes.get("[data-showcase]").innerHTML, "");
  assert.equal(screen.nodes.get("[data-message]").textContent, "Signed out");
  screen.stop();
});

test("record rendering escapes API text, preserves unknown values and marks stale measurements", async () => {
  const screen = await view();
  const html = screen.module.showcaseHTML({ showcase: { name: '<img src=x onerror="evil()">', freshness: { measurements_stale: true }, device_status: { temperature_c: null } },
    profiles: [], drafts: [], readings: [], commands: [], activity: [], scans: [], access: [], errors: [] }, "Viewer");
  assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes("&lt;img"));
  assert.match(html, /measurements are stale/);
  assert.match(html, /Unknown/);
  assert.ok(!html.includes("Demo card holder"));
  screen.stop();
});

test("historical report displays the API's saved snapshot identity and recorded totals", async () => {
  const screen = await view();
  const html = screen.module.showcaseHTML({ showcase: { name: "Demo" }, profiles: [], drafts: [], readings: [], commands: [], activity: [], scans: [], access: [], errors: [],
    saved: fixture("/report-snapshots/demo-test-report"), report: { summary: { recorded_occupied_seconds: 600, proximity_event_count: 2, coverage_seconds: 2100, missing_seconds: 1500, warning_count: 1 } } }, "Viewer");
  assert.match(html, /demo-test-report/);
  assert.match(html, />600</);
  assert.match(html, />2100</);
  assert.match(html, />1500</);
  screen.stop();
});

test("a role-filtered history page never claims that twenty records are visible", async () => {
  const screen = await view();
  const html = screen.module.showcaseHTML({ showcase: {}, profiles: [], drafts: [], readings: [], commands: [], activity: [], scans: [], access: [], errors: [],
    commandsMore: true }, "Viewer");
  assert.match(html, /up to 20 records/);
  assert.match(html, /role may hide some entries/);
  assert.ok(!html.includes("Showing the latest 20 records"));
  screen.stop();
});

test("automatic refresh waits for completion and never overlaps manual or timed loads", async () => {
  const clock = fakeClock();
  let calls = 0, finish;
  const poller = createLiveRefresh({ ...clock, load: () => { calls++; return new Promise((resolve) => { finish = resolve; }); }, cancel() {} });
  poller.setActive(true);
  assert.equal(calls, 1);
  assert.equal(clock.timers.size, 0);
  await poller.run();
  assert.equal(calls, 1);
  finish();
  await settle();
  assert.equal(clock.timers.size, 1);
  assert.equal([...clock.timers.values()][0].delay, 30000);
  clock.fireNext();
  assert.equal(calls, 2);
  assert.equal(clock.timers.size, 0);
  poller.dispose();
  finish();
  await settle();
  assert.equal(clock.timers.size, 0);
});

test("deactivation cancels work, and reactivation waits until the old load settles", async () => {
  const clock = fakeClock();
  let calls = 0, cancellations = 0, finish;
  const poller = createLiveRefresh({ ...clock, load: () => { calls++; return new Promise((resolve) => { finish = resolve; }); }, cancel() { cancellations++; } });
  poller.setActive(true);
  poller.setActive(false);
  assert.equal(cancellations, 1);
  poller.setActive(true);
  assert.equal(calls, 1);
  finish();
  await settle();
  assert.equal(calls, 2);
  poller.setActive(false);
  finish();
  await settle();
  assert.equal(clock.timers.size, 0);
  poller.dispose();
});

test("paused automatic refresh still permits single manual checks", async () => {
  const clock = fakeClock();
  let calls = 0;
  const poller = createLiveRefresh({ ...clock, load: async () => { calls++; }, cancel() {} });
  poller.setActive(true);
  await settle();
  poller.setEnabled(false);
  assert.equal(clock.timers.size, 0);
  await poller.run();
  assert.equal(calls, 2);
  assert.equal(clock.timers.size, 0);
  poller.setActive(false);
  poller.setActive(true);
  assert.equal(calls, 2);
  poller.dispose();
});

test("a failed refresh schedules a later check without spinning", async () => {
  const clock = fakeClock(), errors = [];
  let calls = 0;
  const poller = createLiveRefresh({ ...clock, load: async () => { calls++; throw new Error("Offline"); }, cancel() {}, onError: (error) => errors.push(error.message) });
  poller.setActive(true);
  await settle();
  assert.equal(calls, 1);
  assert.deepEqual(errors, ["Offline"]);
  assert.equal(clock.timers.size, 1);
  assert.equal([...clock.timers.values()][0].delay, 30000);
  poller.dispose();
});

function loadedData(temperature = 24.2) {
  return { showcase: { name: "Demo", device_status: { temperature_c: temperature } }, profiles: [], drafts: [], readings: [],
    commands: [], activity: [], scans: [], access: [], errors: [], loadedAt: "2026-11-04T06:00:00.000000Z" };
}

test("polling replaces readings without a click and retains displayed data during a failed check", async () => {
  const user = { issuer: "test", subject: "test", role: "Viewer", account_version: 1 };
  let calls = 0, release;
  const screen = await view({ user, loader: async () => {
    calls++;
    if (calls === 1) return loadedData();
    if (calls === 2) return new Promise((resolve) => { release = resolve; });
    throw new Error("Network unavailable");
  } });
  await settle();
  assert.match(screen.nodes.get("[data-showcase]").innerHTML, /24.2 °C/);
  screen.clock.fireNext();
  assert.match(screen.nodes.get("[data-showcase]").innerHTML, /24.2 °C/);
  assert.equal(screen.nodes.get("[data-refresh]").disabled, true);
  release(loadedData(25.7));
  await settle();
  assert.match(screen.nodes.get("[data-showcase]").innerHTML, /25.7 °C/);
  screen.clock.fireNext();
  await settle();
  assert.match(screen.nodes.get("[data-showcase]").innerHTML, /25.7 °C/);
  assert.match(screen.nodes.get("[data-message]").textContent, /Previous data remains displayed/);
  assert.equal(screen.clock.timers.size, 1);
  screen.change({ status: "signed_out", user: null, message: "Signed out" });
  assert.equal(screen.nodes.get("[data-showcase]").innerHTML, "");
  assert.equal(screen.clock.timers.size, 0);
  screen.stop();
});

test("hiding the tab aborts its read and returning starts a fresh visible load", async () => {
  const user = { issuer: "test", subject: "test", role: "Viewer", account_version: 1 };
  let calls = 0, release, signal;
  const screen = await view({ user, loader: async (_request, options) => {
    calls++;
    if (calls === 1) return loadedData();
    if (calls === 2) { signal = options.signal; return new Promise((resolve) => { release = resolve; }); }
    return loadedData(25.7);
  } });
  await settle();
  screen.clock.fireNext();
  screen.setHidden(true);
  assert.equal(signal.aborted, true);
  release(loadedData(90));
  await settle();
  assert.equal(calls, 2);
  assert.equal(screen.clock.timers.size, 0);
  assert.match(screen.nodes.get("[data-showcase]").innerHTML, /24.2 °C/);
  screen.setHidden(false);
  await settle();
  assert.equal(calls, 3);
  assert.match(screen.nodes.get("[data-showcase]").innerHTML, /25.7 °C/);
  screen.stop();
  assert.equal(screen.events.size, 0);
  assert.equal(screen.clock.timers.size, 0);
});

test("partial read errors retain prior values explicitly while forbidden sections are cleared", () => {
  const previous = { ...loadedData(), profiles: [{ exhibit_id: "demo-test-clock", name: "Clock", version: 1 }],
    drafts: [{ exhibit_id: "demo-test-clock", content: { title: "Old draft" }, version: 1 }],
    readings: [{ reading_id: "older" }], commands: [{ command_id: "private" }] };
  const latest = { ...loadedData(25.7), profiles: [{ exhibit_id: "demo-test-clock", name: "Renamed clock", version: 2 }],
    errors: [{ section: "Latest readings", status: 503, message: "Busy" }, { section: "Command history", status: 403, message: "Forbidden" },
      { section: "Label demo-test-clock", status: 500, message: "Offline" }] };
  const merged = mergeLiveShowcase(previous, latest);
  assert.equal(merged.showcase.device_status.temperature_c, 25.7);
  assert.equal(merged.profiles[0].name, "Renamed clock");
  assert.equal(merged.drafts[0].content.title, "Old draft");
  assert.equal(merged.readings[0].reading_id, "older");
  assert.deepEqual(merged.commands, []);
  assert.deepEqual(merged.errors.map((error) => Boolean(error.retained)), [true, false, true]);
  assert.equal(previous.profiles[0].version, 1);
});
