import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
// These runtime modules landed on master in PR #9 before this workspace PR.
import { createApi } from "../js/api.js";
import { renderLogin } from "../js/login.js";
import { isOwnStaffRecord } from "../js/pages/staff.js";
import { applyNavigationAvailability } from "../js/pages/overview.js";

const read = async path => (await readFile(path, "utf8")).replaceAll("\r\n", "\n");
test("callback and home have one identical application shell and complete module graph", async () => {
  const home = await read("index.html");
  const callback = await read("auth/callback.html");
  assert.equal(callback, home.replace("  <head>", '  <head>\n    <base href="/" />'));
  for (const html of [home, callback]) {
    for (const tag of ["head", "body", "aside", "main"])
      assert.equal([...html.matchAll(new RegExp("<" + tag + "\\b", "g"))].length, 1, tag);
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    assert.equal(new Set(ids).size, ids.length, "No duplicate element IDs");
    assert.equal([...html.matchAll(/<script type="module"/g)].length, 1);
    assert.match(html, /css\/technology-showcase\.css/);
    assert.match(html, /id="staff-sign-in"/);
    const imports = JSON.parse(html.match(/<script type="importmap">([\s\S]*?)<\/script>/)[1]).imports;
    assert.ok(html.indexOf('<script type="importmap">') < html.indexOf('<script type="module"'));
    // Import-map keys and relative module specifiers normalize to absolute URLs.
    const base = "https://workspace.example.invalid/";
    const normalized = new Map(Object.entries(imports).map(([key, value]) => [new URL(key, base).href, new URL(value, base).href]));
    for (const moduleUrl of Object.values(imports)) {
      const source = await read(moduleUrl.split("?")[0]);
      assert.ok(source.length, "Every mapped module exists in the checkout");
      for (const [, specifier] of source.matchAll(/(?:from\s*|import\s*)["'](\.{1,2}\/[^"']+)["']/g)) {
        const canonical = new URL(specifier, new URL(moduleUrl, base)).href;
        assert.ok(normalized.has(canonical), `${moduleUrl}: ${specifier} resolves to a mapped module`);
        assert.equal(new URL(normalized.get(canonical)).search, new URL(moduleUrl, base).search);
      }
    }
    for (const directory of ["js", "js/pages"]) {
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        if (entry.isFile() && entry.name.endsWith(".js")) assert.ok(imports[`./${directory}/${entry.name}`]);
      }
    }
    assert.equal(new Set(Object.values(imports).map(url => url.split("?v=")[1])).size, 1);
  }
  const app = await read("js/app.js");
  assert.match(app, /render as technologyShowcase/);
  assert.match(app, /await api.initialize\(\)/);
  assert.match(app, /renderLiveAccounts\(dialog.querySelector/);
  assert.doesNotMatch(app, /#page-mode/);
  const staff = await read("js/pages/staff.js");
  assert.doesNotMatch(staff, /renderDemo|data-local-staff/);
  assert.match(staff, /requestClose\(\)\.catch\(reportError\)/);
});

test("Overview disables links to unavailable pages while preserving existing disabled controls", () => {
  const edit = {}, change = {}, label = { disabled: true }, events = {}, alert = {};
  const buttons = new Map([
    ["[data-edit], [data-change]", [edit, change]],
    ["[data-label]", [label]], ["[data-events], [data-alert]", [events, alert]],
  ]);
  const container = { querySelectorAll: selector => buttons.get(selector) };
  applyNavigationAvailability(container, () => false);
  for (const button of [edit, change, label, events, alert]) {
    assert.equal(button.disabled, true);
    assert.match(button.title, /not yet available/);
  }
  const availableEdit = {}, unavailableLabel = { disabled: true };
  const available = { querySelectorAll: () => [availableEdit, unavailableLabel] };
  applyNavigationAvailability(available, () => true);
  assert.equal(availableEdit.disabled, undefined);
  assert.equal(unavailableLabel.disabled, true, "Availability must not override record/permission restrictions");
});

test("workspace uses the merged facade and Demo persona cannot become a Live identity", async () => {
  let requests = 0;
  const session = {
    snapshot: () => ({ user: null }), subscribe: () => () => {},
    completeCallback: async () => false,
    request: async () => { requests++; throw new Error("Unexpected request"); },
  };
  const facade = createApi(session, { location: { pathname: "/" } });
  assert.equal(typeof renderLogin, "function");
  await facade.initialize();
  await facade.signIn("sarah.chen@reshow.demo");
  assert.equal((await facade.getSession()).role, "Administrator");
  assert.equal(facade.can("staff"), true);
  await facade.selectMode("live");
  assert.equal(facade.isLive(), true);
  assert.equal(facade.getSession(), null);
  assert.equal(facade.can("accounts"), false);
  for (const method of ["refreshSession", "listAccounts", "prepareAccountInvitation", "updateAccount"]) {
    assert.equal(typeof facade[method], "function", method);
  }
  await assert.rejects(facade.listAccounts(), error => error.status === 401);
  await assert.rejects(facade.signIn("sarah.chen@reshow.demo"), /secure provider/);
  assert.equal(requests, 0, "A local Administrator never gets a Live request credential");
});

test("Staff self-account protection uses backend staff_id even when provider subject differs", async () => {
  const user = { staff_id: "directory-admin", id: "provider-subject", subject: "provider-subject", permissions: {} };
  const facade = createApi({
    snapshot: () => ({ user }), subscribe: () => () => {}, completeCallback: async () => true,
  }, { location: { pathname: "/auth/callback.html" } });
  await facade.initialize();
  const session = facade.getSession();
  assert.equal(session.id, "directory-admin");
  assert.equal(isOwnStaffRecord({ id: "directory-admin" }, session), true);
  assert.equal(isOwnStaffRecord({ id: "provider-subject" }, session), false);
  assert.equal(isOwnStaffRecord({ id: "another-staff" }, session), false);
  assert.equal(isOwnStaffRecord({ id: "demo-admin" }, { id: "demo-admin" }), true);
  assert.equal(isOwnStaffRecord({}, null), false);
});
