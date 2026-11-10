import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";

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
