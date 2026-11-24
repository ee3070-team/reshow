import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createLiveApi } from "../js/live-api.js";

// Exercise the real Overview render and click handlers with controlled device
// replies. This fixture neither connects to AWS nor supplies producer evidence.
const ui = `
export const esc = value => String(value ?? "");
export const icon = () => "";
export const formatTime = value => value;
export const formatDate = value => value;
export const dateKey = value => value.slice(0, 10);
export const badge = () => "";
export const toast = message => globalThis.lightingFixture.messages.push(message);
export const statusStrip = () => "";
export const metric = () => "";
export const heatmap = () => "";
export const bindHeatmap = () => {};
export const activityNotes = () => "";
export const confirmAction = () => globalThis.lightingFixture.confirm();
`;
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const source = await readFile(new URL("../js/pages/overview.js", import.meta.url), "utf8");
const { render } = await import(moduleUrl(source.replace('"../ui.js"', JSON.stringify(moduleUrl(ui)))));

class Container {
  nodes = new Map();
  buttons = [];
  set innerHTML(html) {
    this.html = html;
    this.buttons = [...html.matchAll(/<button\b([^>]*data-mode="([^"]+)"[^>]*)>/g)].map(([, attrs, mode]) => ({
      dataset: { mode }, disabled: /\bdisabled\b/.test(attrs),
      pressed: attrs.includes('aria-pressed="true"'),
    }));
  }
  get innerHTML() { return this.html; }
  querySelectorAll(selector) { return selector === "[data-mode]" ? this.buttons : []; }
  querySelector(selector) {
    if (!this.nodes.has(selector)) this.nodes.set(selector, { addEventListener() {}, insertAdjacentHTML() {} });
    return this.nodes.get(selector);
  }
}

async function fixture(t, { role = "Administrator", mode = "live", showcase = {}, operations = [] } = {}) {
  const state = {
    role, messages: [], calls: [], confirm: async () => true,
    snapshot: { now: "2026-11-24T04:00:00Z", exhibits: [], operations,
      showcase: { id: "test-case", exhibitId: null, online: true, remoteLighting: true,
        lightingMode: "Auto", lightingOutput: "Off", ...showcase } },
  };
  globalThis.lightingFixture = state;
  const container = new Container();
  const stop = await render(container, { api: {
    getMode: () => mode,
    can: () => state.role !== "Viewer",
    getSnapshot: async () => structuredClone(state.snapshot),
    getActivity: async () => ({ intervals: [], events: [], summary: {} }),
    subscribe: callback => { state.refresh = callback; return () => {}; },
    setLighting: async requested => {
      state.calls.push(requested);
      if (state.send) await state.send();
      state.snapshot.operations.push({ type: "lighting", status: "pending", payload: { mode: requested } });
    },
  } });
  t.after(() => { stop(); delete globalThis.lightingFixture; });
  return { state, container,
    click: mode => { const button = container.buttons.find(item => item.dataset.mode === mode); return button.disabled ? undefined : button.onclick(); },
  };
}

test("unassigned showcase can request each lighting mode and waits for device confirmation", async t => {
  const f = await fixture(t);
  for (const mode of ["On", "Off", "Auto"]) {
    assert.ok(f.container.buttons.every(button => !button.disabled));
    const confirmed = f.state.snapshot.showcase.lightingMode;
    await f.click(mode);
    assert.equal(f.state.calls.at(-1), mode);
    assert.match(f.container.innerHTML, /Pending acknowledgement/);
    assert.equal(f.container.buttons.find(button => button.pressed).dataset.mode, confirmed);
    assert.ok(f.container.buttons.every(button => button.disabled));
    // Only a later server snapshot of producer acknowledgement confirms state.
    f.state.snapshot.operations.at(-1).status = "acknowledged";
    f.state.snapshot.showcase.lightingMode = mode;
    await f.state.refresh();
    assert.equal(f.container.buttons.find(button => button.pressed).dataset.mode, mode);
  }
});

test("unsupported, offline, Viewer and unresolved commands stay disabled with a reason", async t => {
  for (const options of [
    { role: "Viewer" },
    { showcase: { id: null } },
    { showcase: { remoteLighting: false } },
    { showcase: { remoteLighting: null } },
    { showcase: { online: false } },
    { showcase: { measurementStale: true } },
    { operations: [{ type: "lighting", status: "pending", payload: { mode: "On" } }] },
    { operations: [{ type: "lighting", status: "recovery_pending", payload: { mode: "On" } }] },
  ]) {
    await t.test(JSON.stringify(options), async sub => {
      const f = await fixture(sub, options);
      assert.ok(f.container.buttons.every(button => button.disabled));
      assert.match(f.container.innerHTML, /id="lighting-availability" role="status">[^<]+</);
      await f.click("On");
      assert.deepEqual(f.state.calls, []);
    });
  }
});

test("refresh cannot allow another request while confirmation or submission is in flight", async t => {
  const f = await fixture(t);
  let confirm, sent;
  f.state.confirm = () => new Promise(resolve => { confirm = resolve; });
  f.state.send = () => new Promise(resolve => { sent = resolve; });
  const request = f.click("On");
  await f.state.refresh();
  assert.ok(f.container.buttons.every(button => button.disabled));
  await f.click("Off");
  confirm(true);
  await new Promise(setImmediate);
  await f.state.refresh();
  await f.click("Off");
  assert.deepEqual(f.state.calls, ["On"]);
  sent();
  await request;
});

test("cancel and a rejected request preserve confirmed mode and permit a later retry", async t => {
  const f = await fixture(t);
  f.state.confirm = async () => false;
  await f.click("On");
  assert.deepEqual(f.state.calls, []);
  f.state.confirm = async () => true;
  f.state.send = async () => { throw new Error("Device became unavailable"); };
  await f.click("On");
  assert.ok(f.state.messages.includes("Device became unavailable"));
  assert.equal(f.container.buttons.find(button => button.pressed).dataset.mode, "Auto");
  assert.ok(f.container.buttons.every(button => !button.disabled));
});

test("Demo lighting stays usable without a live firmware capability", async t => {
  const f = await fixture(t, { mode: "demo", showcase: { remoteLighting: null } });
  await f.click("On");
  assert.deepEqual(f.state.calls, ["On"]);
});

test("live adapter reads capability from exact device status and queues no exhibit dependency", async () => {
  for (const ready of [true, false, null]) {
    const requests = [];
    const item = { showcase_id: "test-case", device_id: "test-device", confirmed_exhibit_id: null,
      device_status: { remote_lighting: ready }, freshness: { contact_stale: false } };
    const api = createLiveApi({
      minimumRequestIntervalMs: 0,
      getSession: () => ({ id: "lighting-fixture" }), can: () => true,
      request: async (path, options) => {
        if (path.startsWith("/staff-api/showcases?")) return { items: [{ ...item, device_status: {} }], next_cursor: null };
        if (path === "/staff-api/showcases/test-case") return { item };
        assert.equal(path, "/staff-api/commands");
        requests.push(options.body);
        return { item: { ...options.body, status: "pending" } };
      },
    });
    const snapshot = await api.getSnapshot({ include: [] });
    assert.equal(snapshot.showcase.remoteLighting, ready);
    assert.equal(snapshot.showcase.exhibitId, null);
    if (ready) {
      await api.setLighting("On");
      assert.equal(requests[0].showcase_id, "test-case");
      assert.deepEqual(requests[0].payload, { mode: "On" });
    }
    item.lighting_mode = "On";
    item.lighting_acknowledged_at = "2026-11-24T04:00:00.500Z";
    item.device_status.lighting_mode = "Off";
    item.device_status.recorded_at = "2026-11-24T04:00:00.100Z";
    assert.equal((await api.getSnapshot({ include: [] })).showcase.lightingMode, "On",
      "Old queued telemetry cannot roll back a newer acknowledged command");
    item.device_status.recorded_at = "2026-11-24T04:00:10.000Z";
    item.device_status.lighting_mode = "Auto";
    assert.equal((await api.getSnapshot({ include: [] })).showcase.lightingMode, "Auto",
      "A Mega reboot to Auto must supersede the previous cloud On request");
    item.device_status.lighting_mode = "Off";
    assert.equal((await api.getSnapshot({ include: [] })).showcase.lightingMode, "Off",
      "New local overrides must remain visible");
    item.device_status.lighting_mode = null;
    assert.equal((await api.getSnapshot({ include: [] })).showcase.lightingMode, "On");
  }
});
