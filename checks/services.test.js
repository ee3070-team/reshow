import test from 'node:test';
import assert from 'node:assert/strict';
import { aggregateActivity, generateReport, saveStaff } from '../js/services.js';
import { esc } from '../js/ui.js';

test('activity excludes missing/future observations from totals but counts missing coverage', () => {
  const result = aggregateActivity([
    { availability: 'recorded', events: 3, occupiedSeconds: 12 },
    { availability: 'missing', events: 99, occupiedSeconds: 99 },
    { availability: 'future', events: 99, occupiedSeconds: 99 },
  ], [{ type: 'temperature', status: 'warning' }, { type: 'brightness', status: 'acknowledged' }]);
  assert.deepEqual(result, {
    proximityEvents: 3, occupiedSeconds: 12, warnings: 1, missingIntervals: 1,
    recordedIntervals: 1, expectedIntervals: 2, coveragePercent: 50,
  });
  assert.equal(aggregateActivity([], []).coveragePercent, null);
});

test('report rejects reversed and impossible calendar dates', async () => {
  await assert.rejects(generateReport({ start: '2026-10-06', end: '2026-10-01' }), /Start date/);
  await assert.rejects(generateReport({ start: '2026-02-30' }), /valid start and end dates/);
});

test('staff rejects invalid email before saving', async () => {
  await assert.rejects(saveStaff({ name: 'Test', email: '<invalid>', role: 'Viewer' }), /valid email/);
});

test('display escaping treats HTML as text', () => {
  assert.equal(esc('<img src="x" onerror=\'alert(1)\'>&'), '&lt;img src=&quot;x&quot; onerror=&#39;alert(1)&#39;&gt;&amp;');
});

test('staff email validation accepts normal addresses and rejects malformed boundaries', async () => {
  for (const email of ['a@@b.com', '@b.com', 'a@.com', 'a@b.', 'a b@c.com', 'a@b', 'a@b. c']) {
    await assert.rejects(saveStaff({ name: 'Validation test', email, role: 'Viewer' }), /valid email/);
  }
  const saved = await saveStaff({ name: 'Validation test', email: 'valid+tag@example.com', role: 'Viewer' });
  assert.equal(saved.email, 'valid+tag@example.com');
});
