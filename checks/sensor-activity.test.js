import test from "node:test";
import assert from "node:assert/strict";
import { createLiveApi } from "../js/live-api.js";
import { sensorObservations, heatmap } from "../js/ui.js";
import { buildCsv } from "../js/pages/reports.js";
import { render as renderActivity } from "../js/pages/activity.js";
import { render as renderReports } from "../js/pages/reports.js";

function fixture() {
  const raw = {
    device_id: "sensor-device", start: "2026-11-25T16:00:00Z", end: "2026-11-26T16:00:00Z",
    evaluation: { captured_at: "2026-11-26T02:00:00Z", effective_start: "2026-11-25T16:00:00Z", effective_end: "2026-11-26T02:00:00Z" },
    summary: { proximity_event_count: null, recorded_occupied_seconds: null, coverage_seconds: 0,
      missing_seconds: 36000, occupied_sample_count: 1, sensor_sample_count: 2 },
    sensor_sample_semantics: { note: "Point observations; duration unknown.", consistency: "eventual" },
    sensor_samples: [
      { reading_id: "observed", recorded_at: "2026-11-26T01:00:00Z", exhibit_id: "helmet", occupied: true, distance_cm: 12.3, source: "device" },
      { reading_id: "clear", recorded_at: "2026-11-26T01:00:10Z", exhibit_id: "helmet", occupied: false, distance_cm: null, source: "device" },
    ],
    buckets: [{ start: "2026-11-26T01:00:00Z", end: "2026-11-26T02:00:00Z", status: "missing",
      recorded_occupied_seconds: null, proximity_event_count: null, sensor_sample_count: 2, occupied_sample_count: 1 }],
  };
  const showcase = { showcase_id: "case", device_id: "sensor-device" };
  const calls = [];
  const api = createLiveApi({
    minimumRequestIntervalMs: 0, getSession: () => ({ id: "viewer" }), can: p => p === "view",
    request: async path => {
      calls.push(path);
      if (path.startsWith("/staff-api/activity?")) return structuredClone(raw);
      if (path.startsWith("/staff-api/showcases?")) return { items: [showcase], next_cursor: null };
      if (path === "/staff-api/showcases/case") return { item: showcase };
      if (path.startsWith("/staff-api/exhibit-profiles?")) return { items: [], next_cursor: null };
      if (path === "/staff-api/report-snapshots/saved") return {
        report_id: "saved", title: "Saved sensor report", sections: ["activity"], captured_at: raw.evaluation.captured_at,
        report: structuredClone(raw), profile_context: { exhibits: [] },
      };
      throw new Error(`Unexpected request: ${path}`);
    },
  });
  return { api, raw, calls, filters: { showcaseId: "case", start: "2026-11-26", end: "2026-11-26", exhibitId: "" } };
}

class Element {
  nodes = new Map(); innerHTML = ""; elements = {};
  querySelector(key) { if (!this.nodes.has(key)) this.nodes.set(key, new Element()); return this.nodes.get(key); }
  querySelectorAll() { return []; }
  addEventListener() {}
}

test("viewer sees occupied readings in Activity, sensor heatmap and report exports without invented duration", async () => {
  const { api, filters } = fixture();
  const data = await api.getActivity(filters);
  assert.equal(data.events.length, 1);
  assert.equal(data.events[0].type, "sensor");
  assert.match(data.events[0].details, /12.3 cm/);
  assert.equal(data.summary.occupiedSeconds, null);
  assert.equal(data.summary.proximityEvents, null);
  assert.equal(data.summary.coveragePercent, 0);
  assert.match(sensorObservations(data), /1 occupied samples/);
  assert.match(heatmap(data.intervals, "occupiedSamples"), /heatmap-cell level-1/);
  assert.match(heatmap(data.intervals, "occupiedSamples"), /1 occupied sensor samples/);
  const preview = await api.generateReport(filters, ["activity"]);
  const csv = buildCsv(preview);
  assert.match(csv, /"observed","2026-11-26T01:00:00Z","helmet","Yes","12.3","device"/);
  assert.match(csv, /"clear","2026-11-26T01:00:10Z","helmet","No","","device"/);
  const root = new Element();
  const off = await renderActivity(root, { api: { ...api, getSnapshot: async () => ({ exhibits: [], now: "2026-11-26T02:00:00Z", showcase: { id: "case" } }) }, params: filters });
  assert.match(root.querySelector("[data-results]").innerHTML, /Ultrasonic sensor observations/);
  assert.match(root.querySelector("[data-results]").innerHTML, /Ultrasonic sensor detected occupancy/);
  off();
});

test("reopened report renders and exports frozen samples without fetching new activity", async () => {
  const { api, calls } = fixture();
  const saved = await api.getReport("saved");
  const root = new Element();
  await renderReports(root, { api: { ...api, getSnapshot: async () => ({ exhibits: [], reports: [saved], now: "2026-11-26T02:00:00Z", showcase: { id: "case" } }) }, params: {} });
  assert.match(root.querySelector("[data-preview]").innerHTML, /1 occupied samples/);
  assert.match(buildCsv(saved), /"observed"/);
  assert.ok(calls.every(path => !path.startsWith("/staff-api/activity")));
  saved.sections = ["history"];
  assert.doesNotMatch(buildCsv(saved), /"observed"/);
});

test("old snapshots remain usable and sample display escapes producer values", async () => {
  const { api, raw, filters } = fixture();
  delete raw.sensor_sample_semantics;
  delete raw.sensor_samples;
  const old = await api.getActivity(filters);
  assert.equal(sensorObservations(old), "");
  assert.deepEqual(old.sensorSamples, []);
  old.raw.sensor_sample_semantics = { note: "<script>" };
  old.sensorSamples = [{ occupied: true, recorded_at: "2026-11-26T01:00:00Z", exhibit_id: "<img>", source: "device" }];
  assert.match(sensorObservations(old), /&lt;img&gt;/);
  assert.doesNotMatch(sensorObservations(old), /<script>/);
});
