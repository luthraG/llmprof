// llmprof JavaScript / TypeScript SDK.
//
// Label the components of an LLM call (RAG chunks, tools, history, ...) and send
// them to a running llmprof proxy, which does the tokenizing, attribution, waste
// analysis, and pricing - so JS traces look exactly like Python ones in the same
// dashboard. Requires `llmprof up` (or `npx llmprof up`) to be running.

const DEFAULT_URL =
  (typeof process !== 'undefined' && process.env && process.env.LLMPROF_URL) ||
  'http://localhost:4000';

function textOf(content) {
  return typeof content === 'string' ? content : JSON.stringify(content);
}

export class Profile {
  constructor(opts = {}) {
    this.model = opts.model || 'gpt-4o';
    this.provider = opts.provider || 'openai';
    this.session = opts.session;
    this.url = (opts.url || DEFAULT_URL).replace(/\/+$/, '');
    this._items = [];
    this._usage = undefined;
    this._started = Date.now() / 1000;
    this._recorded = false;
  }

  /** Tag a component. `content` may be a string or any JSON-serializable value. */
  add(component, content, opts = {}) {
    this._items.push({
      component,
      name: opts.name ?? opts.label,
      text: textOf(content),
      called: !!opts.called,
    });
    return this;
  }

  /** Mark which tools the model actually called (drives unused-tool waste). */
  called(...names) {
    for (const n of names) {
      const item = this._items.find((i) => i.name === n);
      if (item) item.called = true;
      else this._items.push({ component: 'tool', name: n, text: '', called: true });
    }
    return this;
  }

  /** Set exact usage. Pass a provider usage object/dict (OpenAI or Anthropic). */
  usage(usage) {
    this._usage = usage;
    return this;
  }

  /** Send the trace to the proxy. Idempotent; resolves to the ingest result. */
  async record() {
    if (this._recorded) return this._result;
    this._recorded = true;
    const body = {
      model: this.model,
      provider: this.provider,
      session: this.session,
      ts: this._started,
      components: this._items,
      usage: this._usage,
    };
    const res = await fetch(`${this.url}/llmprof/api/ingest`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`llmprof ingest failed: HTTP ${res.status}`);
    this._result = await res.json();
    return this._result;
  }
}

export function createProfile(opts) {
  return new Profile(opts);
}

/**
 * Run `fn(profile)` and record the trace afterwards. Recording errors are
 * swallowed (set LLMPROF_DEBUG to log them) so profiling never breaks your app.
 */
export async function profile(opts, fn) {
  const p = new Profile(opts);
  try {
    return await fn(p);
  } finally {
    try {
      await p.record();
    } catch (e) {
      if (typeof process !== 'undefined' && process.env && process.env.LLMPROF_DEBUG) {
        // eslint-disable-next-line no-console
        console.error('[llmprof]', e);
      }
    }
  }
}

/** Wrap a function so each call is profiled; the profile is passed as the first arg. */
export function profiled(opts, fn) {
  return (...args) => profile(opts, (p) => fn(p, ...args));
}

export default { Profile, createProfile, profile, profiled };
