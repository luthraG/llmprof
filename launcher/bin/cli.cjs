#!/usr/bin/env node
'use strict';

// llmprof launcher for non-Python users.
//
// `npx llmprof up` runs the real llmprof Python package without asking you to
// install Python. It bootstraps `uv` (a single static binary that provisions
// its own CPython) and runs `uv tool run --from llmprof llmprof <args>`.
//
// Environment overrides:
//   LLMPROF_SPEC       package spec to run (default: llmprof==<this version>);
//                      set to a path or git URL to run a local/dev build.
//   LLMPROF_CACHE_DIR  where the uv binary is cached (default: ~/.cache/llmprof).
//   LLMPROF_UV         path to an existing uv binary to use as-is.

const fs = require('fs');
const os = require('os');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const { spawn, spawnSync } = require('child_process');

const pkg = require('../package.json');
const UV_VERSION = '0.11.21';

// --- pure helpers (exported for tests) ---------------------------------- //

function uvTarget(platform = process.platform, arch = process.arch) {
  const cpu = arch === 'arm64' ? 'aarch64' : arch === 'x64' ? 'x86_64' : null;
  if (!cpu) return null;
  if (platform === 'linux') return { triple: `${cpu}-unknown-linux-gnu`, ext: 'tar.gz', bin: 'uv' };
  if (platform === 'darwin') return { triple: `${cpu}-apple-darwin`, ext: 'tar.gz', bin: 'uv' };
  if (platform === 'win32') return { triple: `${cpu}-pc-windows-msvc`, ext: 'zip', bin: 'uv.exe' };
  return null;
}

function packageSpec(env = process.env, version = pkg.version) {
  return env.LLMPROF_SPEC || `llmprof==${version}`;
}

function cacheDir(env = process.env) {
  return env.LLMPROF_CACHE_DIR || path.join(os.homedir(), '.cache', 'llmprof');
}

function assetName(target, version = UV_VERSION) {
  return `uv-${target.triple}.${target.ext}`;
}

function assetUrl(target, version = UV_VERSION) {
  return `https://github.com/astral-sh/uv/releases/download/${version}/${assetName(target, version)}`;
}

// --- runtime helpers ----------------------------------------------------- //

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const go = (u, n = 0) => {
      if (n > 6) return reject(new Error('too many redirects'));
      https
        .get(u, { headers: { 'User-Agent': 'llmprof-launcher' } }, (res) => {
          if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
            res.resume();
            return go(res.headers.location, n + 1);
          }
          if (res.statusCode !== 200) {
            res.resume();
            return reject(new Error(`HTTP ${res.statusCode} for ${u}`));
          }
          const file = fs.createWriteStream(dest);
          res.pipe(file);
          file.on('finish', () => file.close(() => resolve()));
          file.on('error', reject);
        })
        .on('error', reject);
    };
    go(url);
  });
}

function fetchText(url) {
  return new Promise((resolve, reject) => {
    const go = (u, n = 0) => {
      if (n > 6) return reject(new Error('too many redirects'));
      https
        .get(u, { headers: { 'User-Agent': 'llmprof-launcher' } }, (res) => {
          if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
            res.resume();
            return go(res.headers.location, n + 1);
          }
          if (res.statusCode !== 200) {
            res.resume();
            return reject(new Error(`HTTP ${res.statusCode}`));
          }
          let body = '';
          res.setEncoding('utf8');
          res.on('data', (c) => (body += c));
          res.on('end', () => resolve(body));
        })
        .on('error', reject);
    };
    go(url);
  });
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function findFile(root, name) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(full);
      else if (e.name === name) return full;
    }
  }
  return null;
}

function uvOnPath() {
  const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['uv'], { encoding: 'utf8' });
  if (r.status === 0 && r.stdout) return r.stdout.split(/\r?\n/)[0].trim();
  return null;
}

async function ensureUv() {
  if (process.env.LLMPROF_UV && fs.existsSync(process.env.LLMPROF_UV)) return process.env.LLMPROF_UV;
  const onPath = uvOnPath();
  if (onPath) return onPath;

  const target = uvTarget();
  if (!target) throw new Error(`unsupported platform: ${process.platform}/${process.arch}`);

  const dir = path.join(cacheDir(), 'uv', UV_VERSION);
  const cached = findFile(dir, target.bin);
  if (cached) return cached;

  fs.mkdirSync(dir, { recursive: true });
  const archive = path.join(dir, assetName(target));
  process.stderr.write(`llmprof: fetching uv ${UV_VERSION} (${target.triple})...\n`);
  await download(assetUrl(target), archive);

  // verify the checksum when uv publishes one; skip quietly if unavailable
  try {
    const sums = await fetchText(`${assetUrl(target)}.sha256`);
    const expected = sums.trim().split(/\s+/)[0];
    if (expected && expected.toLowerCase() !== sha256(archive)) {
      throw new Error('uv download failed checksum verification');
    }
  } catch (e) {
    if (/checksum/.test(e.message)) throw e;
  }

  const res = spawnSync('tar', ['-xf', archive, '-C', dir], { stdio: 'inherit' });
  if (res.status !== 0) throw new Error('failed to extract uv (is `tar` available?)');

  const bin = findFile(dir, target.bin);
  if (!bin) throw new Error('uv binary not found after extraction');
  if (process.platform !== 'win32') fs.chmodSync(bin, 0o755);
  return bin;
}

async function main() {
  const args = process.argv.slice(2);
  const forwarded = args.length ? args : ['up']; // bare `npx llmprof` starts the proxy

  let uv;
  try {
    uv = await ensureUv();
  } catch (e) {
    process.stderr.write(`llmprof: ${e.message}\n`);
    process.exit(1);
  }

  const child = spawn(uv, ['tool', 'run', '--from', packageSpec(), 'llmprof', ...forwarded], {
    stdio: 'inherit',
  });
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      try {
        child.kill(sig);
      } catch {
        /* already gone */
      }
    });
  }
  child.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code == null ? 0 : code);
  });
}

if (require.main === module) main();

module.exports = { uvTarget, packageSpec, cacheDir, assetName, assetUrl };
