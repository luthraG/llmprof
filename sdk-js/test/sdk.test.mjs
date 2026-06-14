import { test } from 'node:test';
import assert from 'node:assert';
import http from 'node:http';
import { Profile, profile, profiled } from '../index.js';

function mockServer() {
  const received = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      received.push({ url: req.url, body: body ? JSON.parse(body) : null });
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: true, reclaimable_usd: 0.01 }));
    });
  });
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () =>
      resolve({ server, received, url: `http://127.0.0.1:${server.address().port}` }),
    ),
  );
}

test('profile collects labeled components and posts them', async () => {
  const { server, received, url } = await mockServer();
  try {
    const result = await profile({ model: 'gpt-4o', url }, async (p) => {
      p.add('system prompt', 'You are helpful');
      p.add('rag_chunk', { doc: 'x' }, { name: 'kb#1' });
      p.add('tool', { name: 'search' }, { name: 'search', called: true });
      p.usage({ prompt_tokens: 10, completion_tokens: 2 });
      return 'done';
    });
    assert.strictEqual(result, 'done');
    assert.strictEqual(received.length, 1);
    const b = received[0].body;
    assert.strictEqual(received[0].url, '/llmprof/api/ingest');
    assert.strictEqual(b.model, 'gpt-4o');
    assert.strictEqual(b.components.length, 3);
    assert.deepStrictEqual(b.usage, { prompt_tokens: 10, completion_tokens: 2 });
    assert.strictEqual(b.components.find((c) => c.name === 'kb#1').text, JSON.stringify({ doc: 'x' }));
    assert.strictEqual(b.components.find((c) => c.name === 'search').called, true);
  } finally {
    server.close();
  }
});

test('record is idempotent', async () => {
  const { server, received, url } = await mockServer();
  try {
    const p = new Profile({ model: 'gpt-4o', url });
    p.add('system prompt', 'x'.repeat(10));
    const r1 = await p.record();
    const r2 = await p.record();
    assert.strictEqual(received.length, 1);
    assert.deepStrictEqual(r1, r2);
  } finally {
    server.close();
  }
});

test('profiled passes the profile as the first arg', async () => {
  const { server, received, url } = await mockServer();
  try {
    const answer = profiled({ model: 'gpt-4o', url }, async (p, q) => {
      p.add('user input', q);
      return `ans:${q}`;
    });
    assert.strictEqual(await answer('why'), 'ans:why');
    assert.strictEqual(received.length, 1);
    assert.strictEqual(received[0].body.components[0].text, 'why');
  } finally {
    server.close();
  }
});

test('profile does not throw when the proxy is unreachable', async () => {
  const out = await profile({ model: 'gpt-4o', url: 'http://127.0.0.1:1' }, async (p) => {
    p.add('system prompt', 'x');
    return 42;
  });
  assert.strictEqual(out, 42);
});
