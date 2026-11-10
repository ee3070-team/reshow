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
const fixture = "checks/fixtures/image-upload-browser";

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

test("Chromium exercises exhibit and label image edits through the rendered forms", { timeout: 60000 }, async t => {
  const browser = browserPath();
  if (!browser && !process.env.CI) return t.skip("Install Chromium/Chrome/Edge for the browser integration test");
  assert.ok(browser, "CI must provide Chromium for browser integration tests");
  const root = await realpath(".");
  const prefix = join(tmpdir(), "reshow-image-browser-");
  const profile = await mkdtemp(prefix);
  assert.ok(resolve(profile).startsWith(resolve(prefix)), "Only the generated test profile may be removed");
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".jpg": "image/jpeg" };
  let waitingForResult;
  let reported = false;
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://localhost");
      const path = decodeURIComponent(url.pathname).slice(1);
      if (path === fixture + ".html" && url.searchParams.has("complete") && request.method === "POST") {
        reported = true;
        waitingForResult?.end('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>');
        response.writeHead(204).end();
        return;
      }
      if (path === fixture + ".html" && url.searchParams.has("hold")) {
        response.writeHead(200, { "Content-Type": "image/svg+xml" });
        if (reported) response.end('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>');
        else waitingForResult = response;
        return;
      }
      if ((path !== fixture + ".html" && path !== fixture + ".js" && !/^(?:js|css|assets)\/[A-Za-z0-9_./-]+$/.test(path))
        || path.split("/").includes("..")) throw new Error("Not a public fixture path");
      const file = await realpath(resolve(root, path));
      if (!file.startsWith(root + sep)) throw new Error("Path escapes the fixture root");
      let body = await readFile(file);
      if (path === fixture + ".html") {
        // Keep the browser's load event pending while the fixture uses real
        // time for native dialog close events, then let --dump-dom capture it.
        body = body.toString().replace("</body>", `<img src="${fixture}.html?hold" hidden alt=""></body>`);
      }
      response.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream" });
      response.end(body);
    } catch {
      response.writeHead(404);
      response.end("Not found");
    }
  });
  try {
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const url = `http://127.0.0.1:${server.address().port}/${fixture}.html?test`;
    const { stdout, stderr } = await run(browser, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
      "--disable-background-networking", "--disable-component-update", "--disable-sync",
      `--user-data-dir=${profile}`, "--dump-dom", url,
    ], { timeout: 30000, maxBuffer: 4 * 1024 * 1024, windowsHide: true });
    const result = stdout.match(/<p data-browser-result="([^"]*)"[^>]*>([\s\S]*?)<\/p>/);
    assert.ok(result, `The upload fixture result is missing. Browser stderr: ${stderr}`);
    assert.equal(result[1], "passed", result[2] || "The upload fixture did not report a result");
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
});
