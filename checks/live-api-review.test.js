import test from "node:test";
import assert from "node:assert/strict";
import { createLiveApi } from "../js/live-api.js";

const person = { name: "Sample", email: "sample@example.invalid", role: "Viewer" };
function fixture(handler) {
  const calls = [];
  const api = createLiveApi({
    minimumRequestIntervalMs: 0,
    getSession: () => ({ id: "review-fixture", permissions: { manage_staff: true } }),
    can: permission => permission === "manage_staff",
    request: async (path, options) => {
      calls.push({ path, ...options });
      if (handler) await handler(calls.at(-1));
      return { item: { ...options.body, version: 1 } };
    },
  });
  return { api, calls };
}

test("live staff validation rejects malformed email before sending any request", async () => {
  const { api, calls } = fixture();
  for (const email of ["a@@b.com", "@b.com", "a@.com", "a@b.", "a b@c.com", "a@b", "a@b. c"])
    await assert.rejects(api.saveStaff({ ...person, email }), /valid email/);
  assert.equal(calls.length, 0);
  assert.equal((await api.saveStaff({ ...person, email: "sample+tag@example.invalid" })).email, "sample+tag@example.invalid");
});

test("live creates retain one cryptographic ID across an ambiguous retry", async () => {
  let attempts = 0;
  const { api, calls } = fixture(() => {
    if (++attempts === 1) throw new TypeError("Disconnected");
  });
  await assert.rejects(api.saveStaff(person), error => error.ambiguous === true);
  await api.saveStaff(person);
  assert.equal(calls[0].body.staff_id, calls[1].body.staff_id);
  assert.match(calls[0].body.staff_id, /^staff-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test("accepted writes return independently of slow or rejecting view subscribers", { timeout: 2000 }, async () => {
  const { api } = fixture();
  let release, notified = 0;
  const pending = new Promise(resolve => { release = resolve; });
  const stopSlow = api.subscribe(() => { notified++; return pending; });
  const stopFailed = api.subscribe(async () => { throw new Error("View unavailable"); });
  try {
    assert.equal((await api.saveStaff(person)).email, person.email);
    assert.equal(notified, 1);
  } finally {
    release(); stopSlow(); stopFailed();
  }
});

test("profile apply sends the selected version and reuses its command ID after a lost response", async () => {
  const requests = [];
  const api = createLiveApi({
    minimumRequestIntervalMs: 0,
    getSession: () => ({ id: "profile-fixture" }),
    can: permission => permission === "device_commands",
    request: async (path, options) => {
      const showcase = { showcase_id: "sample-display", confirmed_profile: null };
      if (path.startsWith("/staff-api/showcases?")) return { items: [showcase], next_cursor: null };
      if (path === "/staff-api/showcases/sample-display") return { item: showcase };
      assert.equal(path, "/staff-api/commands");
      requests.push(options.body);
      if (requests.length === 1) throw new TypeError("Response lost");
      return { item: { ...options.body, status: "pending" } };
    },
  });
  await assert.rejects(api.assignExhibit("sample-exhibit", 3));
  await api.assignExhibit("sample-exhibit", 3);
  await api.assignExhibit("sample-exhibit", 4);
  assert.deepEqual(requests[0], requests[1]);
  assert.deepEqual(requests[1].payload, { exhibit_id: "sample-exhibit", profile_version: 3 });
  assert.deepEqual(requests[2].payload, { exhibit_id: "sample-exhibit", profile_version: 4 });
  assert.notEqual(requests[2].command_id, requests[1].command_id);
});
