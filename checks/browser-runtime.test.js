import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile, realpath, mkdtemp, rm } from "node:fs/promises";
import { execFile, spawnSync } from "node:child_process";
import { promisify } from "node:util";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { resolve, join, sep, extname } from "node:path";

const run = promisify(execFile);
function browserPath() {
  if (process.platform === "win32") {
    return ["C:/Program Files/Google/Chrome/Application/chrome.exe",
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
  }
  for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
    const found = spawnSync("which", [name], { encoding: "utf8" });
    if (found.status === 0) return found.stdout.trim();
  }
}

test("Chromium loads home and callback with one versioned API module instance", { timeout: 60000 }, async t => {
  const browser = browserPath();
  if (!browser && !process.env.CI) return t.skip("Install Chromium/Chrome/Edge for the browser integration test");
  assert.ok(browser, "CI must provide Chromium for real module-graph verification");
  const root = await realpath(".");
  const prefix = join(tmpdir(), "reshow-browser-");
  const profile = await mkdtemp(prefix);
  assert.ok(resolve(profile).startsWith(resolve(prefix)), "Only the generated test profile may be removed");
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
  const server = createServer(async (request, response) => {
    try {
      const path = decodeURIComponent(new URL(request.url, "http://localhost").pathname).slice(1) || "index.html";
      if (!/^(?:index\.html|auth\/callback\.html|(?:js|css|assets)\/[A-Za-z0-9_./-]+)$/.test(path)
          || path.split("/").includes("..")) throw new Error("Not a public runtime path");
      const file = await realpath(resolve(root, path));
      if (!file.startsWith(root + sep)) throw new Error("Path escapes the runtime root");
      let body = await readFile(file);
      if (path.endsWith(".html")) {
        const html = body.toString("utf8");
        const imports = JSON.parse(html.match(/<script type="importmap">([\s\S]*?)<\/script>/)[1]).imports;
        // Let the browser itself resolve ../ and import-map keys, then compare
        // namespace identity with the explicitly versioned URL used by the app.
        const probe = `<script type="module">
          import * as relative from "./js/pages/../api.js";
          import * as versioned from ${JSON.stringify(imports["./js/api.js"])};
          document.documentElement.dataset.importMapVerified = String(relative === versioned);
        </script>`;
        body = html.replace("</body>", probe + "</body>");
      }
      response.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream" });
      response.end(body);
    } catch {
      response.writeHead(404); response.end("Not found");
    }
  });
  try {
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    for (const [index, path] of ["/", "/auth/callback.html"].entries()) {
      const { stdout } = await run(browser, [
        "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        `--user-data-dir=${join(profile, String(index))}`, "--virtual-time-budget=3000",
        "--dump-dom", origin + path,
      ], { timeout: 25000, maxBuffer: 4 * 1024 * 1024, windowsHide: true });
      assert.match(stdout, /data-import-map-verified="true"/, `${path}: canonical and relative imports share one instance`);
      assert.match(stdout, /id="login-form"/, `${path}: the actual app module graph rendered the sign-in form`);
      assert.match(stdout, /data-mode="live"/, `${path}: Live entry is available`);
      if (path.includes("callback")) {
        assert.match(stdout, /id="login-mode-badge"[^>]*>Live workspace/, "A failed callback stays in Live sign-in mode");
        assert.match(stdout, /id="live-login-error"[^>]*>[^<]*(?:invalid|expired)/, "Callback failure is visible in the Live form");
      }
    }
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
});
