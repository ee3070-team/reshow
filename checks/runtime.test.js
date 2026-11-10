import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
// These runtime modules landed on master in PR #9 before this workspace PR.
import { createApi } from "../js/api.js";
import { renderLogin } from "../js/login.js";
import { isOwnStaffRecord } from "../js/pages/staff.js";

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
    for (const moduleUrl of Object.values(imports)) {
      assert.ok((await read(moduleUrl.split("?")[0])).length, "Every mapped module exists in the checkout");
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
