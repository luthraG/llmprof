export interface ProfileOptions {
  /** Model id, used for tokenizer choice and pricing. Default "gpt-4o". */
  model?: string;
  /** "openai" (default) or "anthropic", for labeling. */
  provider?: string;
  /** Explicit run id, to group calls into a timeline. */
  session?: string;
  /** Proxy base URL. Default LLMPROF_URL env or http://localhost:4000. */
  url?: string;
}

export interface AddOptions {
  /** Sub-item name; tool/rag components use it as a flame-graph drill-down child. */
  name?: string;
  /** Alias for `name`. */
  label?: string;
  /** Whether the model actually called this tool. */
  called?: boolean;
}

export interface IngestResult {
  ok: boolean;
  reclaimable_usd: number;
}

export class Profile {
  constructor(opts?: ProfileOptions);
  add(component: string, content: unknown, opts?: AddOptions): this;
  called(...names: string[]): this;
  usage(usage: unknown): this;
  record(): Promise<IngestResult>;
}

export function createProfile(opts?: ProfileOptions): Profile;
export function profile<T>(opts: ProfileOptions, fn: (p: Profile) => Promise<T> | T): Promise<T>;
export function profiled<A extends unknown[], T>(
  opts: ProfileOptions,
  fn: (p: Profile, ...args: A) => Promise<T> | T,
): (...args: A) => Promise<T>;

declare const _default: {
  Profile: typeof Profile;
  createProfile: typeof createProfile;
  profile: typeof profile;
  profiled: typeof profiled;
};
export default _default;
