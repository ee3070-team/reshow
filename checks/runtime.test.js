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

test("3D generation supports Workers AI and Tencent without changing JSON generation", async () => {
  const showcase = await readFile("js/pages/technology-showcase.js", "utf8");
  assert.match(showcase, /Image-to-JSON assistant/);
  assert.match(showcase, /Image-to-3D assistant/);
  assert.match(showcase, /data-generate-3d[^>]*>Generate Three\.js Group/);
  assert.match(showcase, /data-3d-provider/);
  assert.match(showcase, /Tencent Hunyuan 3D Rapid/);
  assert.match(showcase, /HUNYUAN_SUBMIT_PATH/);
  assert.match(showcase, /HUNYUAN_QUERY_PATH/);
  assert.match(showcase, /data-hunyuan-secret-key/);
  assert.match(showcase, /serializeJson\(\{/);
  const jsonGenerationStart = showcase.indexOf("generateButton.addEventListener(");
  const jsonGenerationEnd = showcase.indexOf("image3DGenerate.addEventListener(");
  assert.ok(jsonGenerationStart >= 0 && jsonGenerationEnd > jsonGenerationStart);
  const jsonGeneration = showcase.slice(jsonGenerationStart, jsonGenerationEnd);
  assert.match(jsonGeneration, /data\.append\("api_token", apiToken\)/);
  assert.doesNotMatch(jsonGeneration, /data\.append\("task"/);
  assert.match(showcase, /data\.append\("task", "threejs_group"\)/);
  assert.match(showcase, /data\.append\("stage", stage\.name\)/);
  assert.match(showcase, /data\.append\("previous_parts", JSON\.stringify\(completedParts\)\)/);
  assert.match(showcase, /applyGeneratedModel\(result\.data\.parts, stage\.minimumParts\)/);
  assert.match(showcase, /for \(const \[index, stage\] of stages\.entries\(\)\)/);
  assert.match(showcase, /result\.data\.stage !== stage\.name/);
  const modelGenerationStart = showcase.indexOf("image3DGenerate.addEventListener(");
  const modelGenerationEnd = showcase.indexOf("const rfidStatus");
  assert.ok(modelGenerationStart >= 0 && modelGenerationEnd > modelGenerationStart);
  const modelGeneration = showcase.slice(modelGenerationStart, modelGenerationEnd);
  assert.match(modelGeneration, /!loadedImage \|\| !selectedImage/);
  assert.match(modelGeneration, /context\.drawImage\(loadedImage,/);
  assert.match(showcase, /data-image-3d-status/);
  assert.match(showcase, /hunyuanSecretKey\.value = ""/);
});
