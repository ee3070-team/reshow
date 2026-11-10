import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("callback retains the current studio shell and auth starts before routing", async () => {
  const home = await readFile("index.html", "utf8");
  const callback = await readFile("auth/callback.html", "utf8");
  assert.equal(callback, home.replace("  <head>", '  <head>\n    <base href="/" />'));
  assert.match(home, /css\/technology-showcase\.css/);
  assert.match(home, /id="staff-sign-in"/);
  const app = await readFile("js/app.js", "utf8");
  assert.match(app, /render as technologyShowcase/);
  assert.match(app, /initializeStaffLogin\(\)\s*\.then\(\(\) => navigate/);
  const staff = await readFile("js/pages/staff.js", "utf8");
  assert.match(staff, /renderLiveAccounts/);
  assert.match(staff, /Browser-local staff and physical-card demo/);
  assert.match(staff, /requestClose\(\)\.catch\(reportError\)/);
});
