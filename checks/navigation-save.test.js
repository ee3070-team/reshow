import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// Execute the application's navigation gate with controlled dialog timing.
const source = await readFile(new URL("../js/app.js", import.meta.url), "utf8");
const gate = source.slice(source.indexOf("async function canLeave()"), source.indexOf("async function navigate("));
const makeGate = new Function("confirmAction", "toast", `
  let dirty = true, pageBusy = false;
  ${gate}
  return { canLeave, busy: value => { pageBusy = value; }, dirty: value => { dirty = value; } };
`);

test("navigation cannot discard an active image save, including while a dialog is open", async () => {
  let finishDialog, dialogs = 0, notices = 0;
  const page = makeGate(() => { dialogs++; return new Promise(resolve => { finishDialog = resolve; }); }, () => { notices++; });
  page.busy(true);
  assert.equal(await page.canLeave(), false);
  assert.equal(dialogs, 0);
  assert.equal(notices, 1);
  page.busy(false);
  const leaving = page.canLeave();
  page.busy(true);
  finishDialog(true);
  assert.equal(await leaving, false);
  page.busy(false);
  page.dirty(false);
  assert.equal(await page.canLeave(), true);
});
