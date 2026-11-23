import test from 'node:test';
import assert from 'node:assert/strict';
import { displayPeriod } from '../js/pages/reports.js';
import { handleAction, staffIdentity } from '../js/ui.js';

test('identity display handles missing or blank backend names without interrupting navigation', () => {
  for (const user of [null, {}, { name: null }, { name: 42 }, { name: '  ' }]) {
    assert.deepEqual(staffIdentity(user), { name: 'Staff member', initials: 'SM' });
  }
  assert.deepEqual(staffIdentity({ name: '  Ada  Chan  ' }), { name: 'Ada  Chan', initials: 'AC' });
});

test('report display period clips ISO timestamps to report window and creation time', () => {
  const report = { filters: { start: '2026-10-02', end: '2026-10-04' }, createdAt: '2026-10-03T08:00:00.000Z' };
  assert.deepEqual(displayPeriod({ start: '2026-10-01T00:00:00.000Z', end: '2026-10-05T00:00:00.000Z' }, report), {
    start: '2026-10-01T16:00:00.000Z', end: report.createdAt,
  });
});

test('event action converts an asynchronous failure into a visible error toast', async () => {
  const originalDocument = globalThis.document;
  const originalTimeout = globalThis.setTimeout;
  const messages = [];
  const element = { remove() {}, append() {}, className: '', textContent: '' };
  let created = false;
  globalThis.document = {
    createElement: () => {
      if (created) return { setAttribute() {} };
      created = true;
      return element;
    },
    querySelector: () => ({ append: (item) => messages.push(item) }),
  };
  globalThis.setTimeout = () => 0;
  try {
    await handleAction(async () => { throw new Error('Service unavailable'); })();
    assert.equal(messages.length, 1);
    assert.match(element.textContent, /Service unavailable/);
    assert.match(element.className, /error/);
  } finally {
    globalThis.document = originalDocument;
    globalThis.setTimeout = originalTimeout;
  }
});
