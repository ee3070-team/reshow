import { cp, mkdir, rm, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';

// Publish only the runtime allowlist, never repository/configuration files.
await rm('dist', { recursive: true, force: true });
await mkdir('dist');
await mkdir('dist/auth');
await cp('auth/callback.html', 'dist/auth/callback.html');
for (const path of ['index.html', 'css', 'js', 'assets']) {
  await cp(path, `dist/${path}`, { recursive: true, dereference: false });
}
const commit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
await writeFile('dist/release.json', JSON.stringify({ commit }) + '\n');
