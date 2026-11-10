import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Inject the existing module's dependencies while exercising its actual DOM handlers.
async function screen({ failure = 'load', hasAccount = false } = {}) {
  const errors = [], calls = [];
  const inviteButton = { disabled: false, isConnected: true };
  const updateButton = { disabled: false, isConnected: true };
  const fields = [{ disabled: false }];
  const inviteForm = { values: { name: 'Synthetic Staff', email: 'test@example.invalid', role: 'Viewer' },
    querySelector: () => inviteButton, querySelectorAll: () => fields };
  const updateForm = { values: { role: 'Operator' }, querySelector: () => updateButton };
  const editor = { innerHTML: '', querySelector: (name) => name === 'form' ? updateForm : null };
  const personButton = { dataset: { account: 'staff01' } };
  const content = { innerHTML: '', childElementCount: 0, replaceChildren() {}, append() {},
    querySelector: (name) => name === '[data-live-invite]' ? inviteForm : editor,
    querySelectorAll: () => hasAccount ? [personButton] : [] };
  const message = { className: '' };
  Object.defineProperty(message, 'textContent', {
    set(value) {
      // A rejected rendering boundary must not become an unhandled promise.
      if (String(value).includes('Service unavailable')) throw new Error('Notice rendering failed');
    },
  });
  const actions = { innerHTML: '', querySelector: () => null };
  const container = { className: '', innerHTML: '', replaceChildren() {}, querySelector: (selector) => ({
    '[data-live-message]': message, '[data-live-actions]': actions, '[data-live-account-content]': content,
  })[selector] };
  const staffSession = {
    snapshot: () => ({ status: 'authenticated', message: 'Session ready', user: {
      name: 'Synthetic Administrator', issuer: 'test', subject: 'test', role: 'Administrator', permissions: { manage_accounts: true },
    } }),
    subscribe: () => () => {},
    async request(path, options) {
      calls.push({ path, options });
      if (failure === 'load' || options) throw new Error('Service unavailable');
      return { next_cursor: null, items: path.startsWith('/staff?')
        ? (hasAccount ? [{ staff_id: 'staff01', name: 'Synthetic Staff', email: 'test@example.invalid' }] : [])
        : (hasAccount ? [{ staff_id: 'staff01', subject: 'synthetic', role: 'Viewer', status: 'active', version: 7 }] : []) };
    },
  };
  globalThis.liveAccountsTest = { document: { createElement: () => ({}) }, staffSession, esc: String, reportError: (error) => errors.push(error.message) };
  const original = await readFile('js/live-accounts.js', 'utf8');
  const source = 'const document = globalThis.liveAccountsTest.document;\n' + original
    .replace('import { staffSession } from "./auth-session.js";', 'const { staffSession } = globalThis.liveAccountsTest;')
    .replace('import { esc, reportError } from "./ui.js";', 'const { esc, reportError } = globalThis.liveAccountsTest;');
  const module = await import('data:text/javascript;base64,' + Buffer.from(source + '\n//# sourceURL=live-accounts-fixture-' + crypto.randomUUID() + '.js').toString('base64'));
  delete globalThis.liveAccountsTest;
  const stop = await module.renderLiveAccounts(container);
  const settle = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
  await settle();
  return { errors, calls, inviteButton, updateButton, inviteForm, updateForm, personButton, stop, settle };
}

class FormValues {
  constructor(form) { this.values = form.values; }
  get(name) { return this.values[name]; }
}

test('account loading catches a rejected rendering boundary without an unhandled promise', async () => {
  const page = await screen();
  assert.deepEqual(page.errors, ['Notice rendering failed']);
  assert.ok(page.calls.every(({ options }) => options === undefined));
  page.stop();
});

test('invitation submission catches a rejected boundary and does not retry an ambiguous mutation', async () => {
  const original = globalThis.FormData;
  globalThis.FormData = FormValues;
  const page = await screen({ failure: 'mutation' });
  try {
    page.inviteForm.onsubmit({ preventDefault() {} });
    await page.settle();
    assert.deepEqual(page.errors, ['Notice rendering failed']);
    assert.equal(page.inviteButton.disabled, false);
    const writes = page.calls.filter(({ options }) => options);
    assert.equal(writes.length, 1);
    assert.equal(writes[0].path, '/staff');
    assert.ok(!page.calls.some(({ path }) => path === '/accounts/invitations'));
  } finally { page.stop(); globalThis.FormData = original; }
});

test('role submission catches a rejected boundary and keeps the exact account revision', async () => {
  const original = globalThis.FormData;
  globalThis.FormData = FormValues;
  const page = await screen({ failure: 'mutation', hasAccount: true });
  try {
    page.personButton.onclick();
    page.updateForm.onsubmit({ preventDefault() {}, currentTarget: page.updateForm });
    await page.settle();
    assert.deepEqual(page.errors, ['Notice rendering failed']);
    assert.equal(page.updateButton.disabled, false);
    const writes = page.calls.filter(({ options }) => options);
    assert.equal(writes.length, 1);
    assert.equal(writes[0].options.version, 7);
    assert.equal(writes[0].options.method, 'PATCH');
  } finally { page.stop(); globalThis.FormData = original; }
});
