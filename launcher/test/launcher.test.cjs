'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { uvTarget, packageSpec, assetName, assetUrl } = require('../bin/cli.cjs');

test('uvTarget maps the supported platforms', () => {
  assert.deepStrictEqual(uvTarget('linux', 'x64'), {
    triple: 'x86_64-unknown-linux-gnu', ext: 'tar.gz', bin: 'uv',
  });
  assert.deepStrictEqual(uvTarget('linux', 'arm64'), {
    triple: 'aarch64-unknown-linux-gnu', ext: 'tar.gz', bin: 'uv',
  });
  assert.deepStrictEqual(uvTarget('darwin', 'arm64'), {
    triple: 'aarch64-apple-darwin', ext: 'tar.gz', bin: 'uv',
  });
  assert.deepStrictEqual(uvTarget('win32', 'x64'), {
    triple: 'x86_64-pc-windows-msvc', ext: 'zip', bin: 'uv.exe',
  });
});

test('uvTarget returns null for unsupported arch/platform', () => {
  assert.strictEqual(uvTarget('linux', 'ia32'), null);
  assert.strictEqual(uvTarget('sunos', 'x64'), null);
});

test('packageSpec defaults to the pinned version and honors the override', () => {
  assert.strictEqual(packageSpec({}, '1.2.3'), 'llmprof==1.2.3');
  assert.strictEqual(packageSpec({ LLMPROF_SPEC: '/local/llmprof' }, '1.2.3'), '/local/llmprof');
  assert.strictEqual(
    packageSpec({ LLMPROF_SPEC: 'git+https://github.com/luthraG/llmprof' }, '1.2.3'),
    'git+https://github.com/luthraG/llmprof',
  );
});

test('asset name and URL are well formed', () => {
  const t = uvTarget('linux', 'x64');
  assert.strictEqual(assetName(t), 'uv-x86_64-unknown-linux-gnu.tar.gz');
  assert.match(assetUrl(t), /^https:\/\/github\.com\/astral-sh\/uv\/releases\/download\/[\d.]+\/uv-x86_64-unknown-linux-gnu\.tar\.gz$/);
});
