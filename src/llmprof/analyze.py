"""Waste detection over a profiled request.

Turns a token breakdown into concrete findings, each with how many tokens (and
dollars) it could reclaim. The detector is provider-agnostic and runs off the
hot path (at record time), so the dashboard and the SDK share one source of
truth for "what is wasteful here and what would fixing it save."

Two tiers of reclaimable:
  - removable tokens (duplicated content, tool schemas never called) -> tokens
    you could literally drop from the call.
  - caching savings (an uncached stable prefix) -> recurring dollars prompt
    caching would save after the first call; no tokens are removed.
Both contribute to the per-call reclaimable dollar figure; only the first
contributes reclaimable tokens.
"""

from __future__ import annotations

import hashlib

from . import tokens

# content shorter than this is not worth flagging as a duplicate
_MIN_DUP_CHARS = 200


def _usd(toks: int, input_per_1k: float | None) -> float | None:
    if input_per_1k is None or not toks:
        return None
    return round(toks / 1000 * input_per_1k, 6)


def _finding(sev, title, body, reclaimable_tokens=0, save_usd=None) -> dict:
    return {
        "severity": sev,
        "title": title,
        "body": body,
        "reclaimable_tokens": reclaimable_tokens,
        "save_usd": save_usd,
    }


def duplicate_tokens(texts: list[str], model: str = "gpt-4o") -> int:
    """Tokens that repeat: for any content block appearing N times, the N-1
    extra copies are reclaimable. Short blocks are ignored to avoid noise."""
    seen: dict[str, int] = {}
    for text in texts or []:
        norm = " ".join((text or "").split())
        if len(norm) < _MIN_DUP_CHARS:
            continue
        key = hashlib.sha1(norm.encode("utf-8", "ignore")).hexdigest()
        seen[key] = seen.get(key, 0) + 1
    wasted = 0
    # re-walk to attribute token cost to the duplicated blocks
    counted: dict[str, int] = {}
    for text in texts or []:
        norm = " ".join((text or "").split())
        if len(norm) < _MIN_DUP_CHARS:
            continue
        key = hashlib.sha1(norm.encode("utf-8", "ignore")).hexdigest()
        if seen.get(key, 0) > 1 and key not in counted:
            counted[key] = tokens.count_tokens(norm, model)
            wasted += counted[key] * (seen[key] - 1)
    return wasted


def analyze(tree: dict, texts: list[str] | None = None, *, input_per_1k: float | None = None,
            cached_tokens: int | None = None, cache_write: int | None = None,
            called_tools: list[str] | None = None, model: str = "gpt-4o",
            prompt_tokens: int | None = None) -> dict:
    """Return {findings, reclaimable_tokens, reclaimable_usd} for one request.

    Caching counts as active if the request had cache reads OR writes, so we do
    not claim caching savings on a prefix that is already being cached.
    """
    tree = tree or {"tokens": 0, "children": []}
    children = tree.get("children") or []
    comp = {c["name"]: c.get("tokens", 0) for c in children}
    total = tree.get("tokens") or sum(comp.values()) or 1
    ts = comp.get("tool schemas", 0)
    caching_active = bool(cached_tokens) or bool(cache_write)
    findings: list[dict] = []

    dup = duplicate_tokens(texts or [], model)
    if dup:
        findings.append(_finding(
            "warn", "Duplicated content in the context",
            f"{dup:,} tokens of content appear more than once in this request. "
            "Dedupe repeated context, instructions, or retrieved chunks.",
            reclaimable_tokens=dup, save_usd=_usd(dup, input_per_1k),
        ))

    ts_node = next((c for c in children if c["name"] == "tool schemas"), None)
    if ts_node and called_tools:
        called = set(called_tools)
        unused = [c for c in (ts_node.get("children") or []) if c["name"] not in called]
        if unused:
            wasted = sum(c.get("tokens", 0) for c in unused)
            names = ", ".join(c["name"] for c in unused[:6]) + (", ..." if len(unused) > 6 else "")
            ntools = len(ts_node.get("children") or [])
            findings.append(_finding(
                "warn", f"{len(unused)} of {ntools} tools were not called",
                f"{names}: {wasted:,} tokens of schemas the model never used on this "
                "request. Drop tools it does not need here, or load them lazily.",
                reclaimable_tokens=wasted, save_usd=_usd(wasted, input_per_1k),
            ))

    if ts / total >= 0.35:
        findings.append(_finding(
            "warn", f"Tool schemas are {ts / total * 100:.0f}% of the context",
            f"{ts:,} tokens. Trim descriptions and parameter lists, or split rarely "
            "used tools into a separate call.",
        ))

    if caching_active:
        served = cached_tokens or 0
        if served:
            # the real billed prompt is the right denominator; the component tree
            # is a tiktoken estimate and can undershoot, which printed >100%.
            denom = prompt_tokens or sum(comp.values()) or 1
            pct = min(served / denom * 100, 100)
            body = f"{served:,} tokens ({pct:.0f}% of the prompt) were served from cache."
        else:
            body = "the stable prefix is being written to cache on this call."
        findings.append(_finding("ok", "Prompt caching is active", body))

    prefix = comp.get("system prompt", 0) + ts
    if prefix >= 1024 and not caching_active:
        save = _usd(int(prefix * 0.9), input_per_1k)
        findings.append(_finding(
            "tip", "Stable prefix is not cached",
            f"System prompt and tool schemas are {prefix:,} tokens that repeat every "
            "call. Prompt caching can cut about 90% off them after the first call.",
            save_usd=save,
        ))

    hist = comp.get("history (assistant)", 0) + comp.get("tool results", 0)
    if hist / total >= 0.4:
        findings.append(_finding(
            "warn", f"History and tool results are {hist / total * 100:.0f}% of the context",
            f"{hist:,} tokens. Summarize or truncate older turns to slow context creep.",
        ))

    if comp.get("system prompt", 0) >= 1500:
        findings.append(_finding(
            "tip", f"Large system prompt ({comp['system prompt']:,} tokens)",
            "This fixed overhead rides on every call. Move examples to a cached prefix "
            "or trim instructions you can express more tersely.",
        ))

    if not any(f["severity"] != "ok" for f in findings):
        findings.append(_finding("ok", "No obvious waste detected", "This context looks lean."))

    # The headline reclaimable is REMOVABLE tokens only (duplicates + unused tool
    # schemas), priced once. We deliberately do NOT add the caching tip's saving:
    # it overlaps with the prefix those tokens belong to (double counting), and it
    # is a recurring cache-hit saving, not tokens you drop. The caching tip still
    # shows its own per-call estimate inline.
    reclaimable_tokens = sum(f["reclaimable_tokens"] for f in findings)
    reclaimable_usd = _usd(reclaimable_tokens, input_per_1k) or 0.0
    return {
        "findings": findings,
        "reclaimable_tokens": reclaimable_tokens,
        "reclaimable_usd": reclaimable_usd,
    }
