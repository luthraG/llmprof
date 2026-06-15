"""Generate the bundled model-pricing snapshot from LiteLLM's database.

LiteLLM's `model_prices_and_context_window.json` (MIT, Berri AI) is the ecosystem's
canonical, provider-sourced pricing table. This script fetches it, keeps the
text-generation models, normalizes each key to a base model id (stripping provider/
region/date noise), converts per-token costs to per-1k, and writes a compact snapshot
to `src/llmprof/data/model_prices.json`.

The snapshot is vendored so llmprof prices thousands of models offline, with no network
call at import (ccusage's documented failure was hard-depending on the live API). Refresh
is a build-time step: re-run this script and commit the diff.

    python scripts/pricing/sync_pricing.py --date 2026-06-15           # fetch live
    python scripts/pricing/sync_pricing.py --date 2026-06-15 --source litellm.json

The pure `transform()` is kept separate from the network fetch so it can be unit-tested.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import urllib.request

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
ATTRIBUTION = (
    "Generated from LiteLLM's model_prices_and_context_window.json (MIT, "
    "Copyright (c) 2023 Berri AI). Per-1k USD. Regenerate with scripts/pricing/sync_pricing.py."
)
TEXT_MODES = {"chat", "responses", "completion"}
# pseudo-models and generic words that would substring-match unrelated ids if kept
# as keys (e.g. "auto"/"free" are aggregator routes, not models).
SKIP_KEYS = {
    "auto", "free", "default", "standard", "basic", "none", "custom", "unknown",
    "chat", "completion", "router", "openrouter", "models",
}

# bedrock dotted vendor prefixes, and region prefixes that ride in front of them
_VENDOR_DOT = re.compile(
    r"^(anthropic|amazon|meta|cohere|mistral|ai21|deepseek|qwen|writer|stability)\."
)
_REGION_DOT = re.compile(r"^(us|eu|apac|au|jp|global|ca|sa|me)\.")
# trailing date / version stamps: -20241022, -2024-10-22, @20240620, -v1:0, :0, -v2
_DATE_TAIL = re.compile(r"([-@](v\d+:\d+|\d{8}|\d{4}-\d{2}-\d{2})|:\d+|-v\d+)$")


def normalize_key(raw: str) -> str:
    """Reduce a LiteLLM model id to a base key that matches what clients send.

    `openrouter/anthropic/claude-3.5-sonnet` -> `claude-3.5-sonnet`,
    `us.anthropic.claude-opus-4-7-20250101-v1:0` -> `claude-opus-4-7`.
    """
    key = raw.strip().lower()
    if "/" in key:                       # provider path prefix(es): keep the last segment
        key = key.rsplit("/", 1)[1]
    key = _REGION_DOT.sub("", key)       # bedrock region prefix
    key = _VENDOR_DOT.sub("", key)       # bedrock dotted vendor prefix
    while True:                          # peel one or more trailing stamps
        stripped = _DATE_TAIL.sub("", key)
        if stripped == key:
            break
        key = stripped
    return key


def _completeness(entry: dict) -> int:
    return sum(1 for k in ("cache_read", "cache_write", "ctx") if entry.get(k) is not None)


def transform(litellm: dict) -> dict:
    """Pure: LiteLLM dict -> {base_key: {in, out, cache_read?, cache_write?, ctx?}}.

    Keeps text-generation models with a real input price, normalizes keys, converts
    per-token to per-1k, and dedupes colliding base keys by keeping the most complete."""
    models: dict[str, dict] = {}
    for raw, spec in litellm.items():
        if raw == "sample_spec" or not isinstance(spec, dict):
            continue
        if spec.get("mode") not in TEXT_MODES:
            continue
        inp = spec.get("input_cost_per_token")
        out = spec.get("output_cost_per_token")
        if inp is None or out is None:   # not a token-priced text model
            continue
        key = normalize_key(raw)
        if len(key) < 3 or key in SKIP_KEYS:   # too short / generic -> bad substring matches
            continue
        entry: dict = {"in": round(inp * 1000, 8), "out": round(out * 1000, 8)}
        cr = spec.get("cache_read_input_token_cost")
        cw = spec.get("cache_creation_input_token_cost")
        ctx = spec.get("max_input_tokens") or spec.get("max_tokens")
        if cr is not None:
            entry["cache_read"] = round(cr * 1000, 8)
        if cw is not None:
            entry["cache_write"] = round(cw * 1000, 8)
        if isinstance(ctx, int) and ctx > 0:
            entry["ctx"] = ctx
        # dedupe: keep the more complete entry on collision
        if key not in models or _completeness(entry) > _completeness(models[key]):
            models[key] = entry
    return dict(sorted(models.items()))


def build_snapshot(litellm: dict, date: str) -> dict:
    models = transform(litellm)
    return {
        "_source": LITELLM_URL,
        "_attribution": ATTRIBUTION,
        "_snapshot_date": date,
        "_count": len(models),
        "models": models,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="snapshot date, e.g. 2026-06-15")
    ap.add_argument("--source", help="local LiteLLM json (skip the network fetch)")
    ap.add_argument("--out", help="output path (default: src/llmprof/data/model_prices.json)")
    args = ap.parse_args()

    if args.source:
        litellm = json.loads(pathlib.Path(args.source).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(LITELLM_URL, timeout=30) as r:  # noqa: S310 - pinned https
            litellm = json.loads(r.read().decode("utf-8"))

    snapshot = build_snapshot(litellm, args.date)
    root = pathlib.Path(__file__).resolve().parents[2]
    default_out = root / "src" / "llmprof" / "data" / "model_prices.json"
    out = pathlib.Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(root)} with {snapshot['_count']} models")


if __name__ == "__main__":
    main()
