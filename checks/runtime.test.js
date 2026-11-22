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

test("3D studio supports fullscreen four-view playback", async () => {
  const showcase = await readFile("js/pages/technology-showcase.js", "utf8");
  assert.match(showcase, /data-fullscreen[^>]*>Play 4-view fullscreen/);
  assert.match(showcase, /viewer\.requestFullscreen\(\)/);
  assert.match(showcase, /document\.addEventListener\("fullscreenchange", syncFullscreen/);
  assert.match(showcase, /for \(const view of FOUR_VIEWS\)/);
  assert.match(showcase, /if \(autoRotate && !dragging\)/);
  assert.match(showcase, /renderer\.domElement\.hidden = true/);
  assert.match(showcase, /renderer\.domElement\.hidden = false/);
  assert.match(showcase, /event\.target\.closest\("\.technology-fullscreen-exit"\)\) return/);
  assert.match(showcase, /Press Escape to exit/);
  const styles = await readFile("css/technology-showcase.css", "utf8");
  assert.match(styles, /\.technology-viewer:fullscreen/);
  assert.match(styles, /\.technology-fullscreen-exit/);
});

test("studio retains JSON and Tripo generation without Workers AI 3D generation", async () => {
  const showcase = await readFile("js/pages/technology-showcase.js", "utf8");
  assert.match(showcase, /Image-to-JSON assistant/);
  assert.match(showcase, /Tripo 3D generation/);
  assert.match(showcase, /data-tripo-retexture/);
  assert.match(showcase, /data-tripo-source/);
  assert.doesNotMatch(showcase, /data-tripo-mode|Text to 3D/);
  assert.doesNotMatch(showcase, /Image-to-3D assistant|data-generate-3d|data-model-chat|threejs_group/);
  assert.doesNotMatch(showcase, /data-agree-terms|\/api\/technology\/agree-terms/);
  assert.doesNotMatch(showcase, /Gemini|data-gemini/);
  const jsonGenerationStart = showcase.indexOf("generateButton.addEventListener(");
  const jsonGenerationEnd = showcase.indexOf("const rfidStatus");
  assert.ok(jsonGenerationStart >= 0 && jsonGenerationEnd > jsonGenerationStart);
  const jsonGeneration = showcase.slice(jsonGenerationStart, jsonGenerationEnd);
  assert.match(jsonGeneration, /data\.append\("api_token", apiToken\)/);
  assert.doesNotMatch(jsonGeneration, /data\.append\("task"/);
});

test("RFID panel explains door and e-paper card roles", async () => {
  const showcase = await readFile("js/pages/technology-showcase.js", "utf8");
  assert.match(showcase, /<dt>Door card<\/dt><dd>Opens the door for about 3 seconds/);
  assert.match(showcase, /<dt>E-paper card<\/dt><dd>Does not open the door/);
  assert.match(showcase, /updates the e-paper automatically\. No second tap is needed/);
  assert.match(showcase, /data-rfid-edit/);
  assert.match(showcase, /data-rfid-description/);
});
