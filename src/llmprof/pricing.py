"""Provider pricing for cost attribution (USD per 1K tokens).

These are estimates that drift over time, so two things matter more than the
exact numbers baked in here:
  1. Unknown models degrade gracefully (cost is omitted, tokens still shown).
  2. You can override or extend prices without touching code by pointing
     LLMPROF_PRICING at a JSON file, e.g.
        {"my-model": [0.001, 0.003], "gpt-4o": [0.0025, 0.01]}
     (values are [input_per_1k, output_per_1k]).
"""

from __future__ import annotations

import json
import os
import re

# trailing annotations some clients append to a model id, e.g. the 1M-context
# beta "claude-sonnet-4-6[1m]". They are a mode, not a different model, so they
# are stripped for grouping/display; pricing already substring-matches the base.
_MODEL_ANNOTATION = re.compile(r"\s*\[[^\]]*\]\s*$")


def normalize_model(model: str | None) -> str | None:
    """Strip a trailing [..] annotation so model variants group together."""
    if not model:
        return model
    return _MODEL_ANNOTATION.sub("", model).strip() or model

# (input_per_1k, output_per_1k). Substring-matched against the model id, longest
# key first, so "gpt-4o-mini" beats "gpt-4o" and "claude-sonnet-4" also matches
# "claude-sonnet-4-5". These are estimates that drift; cross-checked against the
# providers' official pricing pages (noted 2026-06). Hosted open-weight models
# vary in price by provider (DeepInfra / Fireworks / Cerebras / Together); the
# values below are representative, so use LLMPROF_PRICING to pin exact numbers.
PRICES: dict[str, tuple[float, float]] = {
    # --- OpenAI ---
    "gpt-5.5-pro": (0.03, 0.18),
    "gpt-5.5": (0.005, 0.03),
    "gpt-5.4-pro": (0.03, 0.18),
    "gpt-5.4-nano": (0.0002, 0.00125),
    "gpt-5.4-mini": (0.00075, 0.0045),
    "gpt-5.4": (0.0025, 0.015),
    "gpt-5.3-codex": (0.00175, 0.014),
    "gpt-4.1-nano": (0.0001, 0.0004),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4o-mini": (0.00015, 0.0006),
    "chatgpt-4o-latest": (0.005, 0.015),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "o4-mini": (0.0011, 0.0044),
    "o3-mini": (0.0011, 0.0044),
    "o3": (0.002, 0.008),
    "o1-pro": (0.15, 0.6),
    "o1-mini": (0.0011, 0.0044),
    "o1": (0.015, 0.06),
    "gpt-oss-120b": (0.00015, 0.0006),
    "gpt-oss-20b": (0.00005, 0.0002),
    # --- Anthropic (family keys also match newer dotted subversions) ---
    "claude-fable-5": (0.01, 0.05),
    "claude-opus-4-8": (0.005, 0.025),
    "claude-opus-4-7": (0.005, 0.025),
    "claude-opus-4-6": (0.005, 0.025),
    "claude-opus-4-5": (0.005, 0.025),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-7-sonnet": (0.003, 0.015),
    "claude-3-sonnet": (0.003, 0.015),
    "claude-3-opus": (0.015, 0.075),
    "claude-3-haiku": (0.00025, 0.00125),
    "claude-haiku-4": (0.001, 0.005),
    "claude-sonnet-4": (0.003, 0.015),
    "claude-opus-4": (0.015, 0.075),
    # --- Google Gemini ---
    "gemini-3.5-flash": (0.0015, 0.009),
    "gemini-3.1-pro": (0.002, 0.012),
    "gemini-3.1-flash-lite": (0.00025, 0.0015),
    "gemini-3-flash": (0.0005, 0.003),
    "gemini-2.5-pro": (0.00125, 0.01),
    "gemini-2.5-flash-lite": (0.0001, 0.0004),
    "gemini-2.5-flash": (0.0003, 0.0025),
    "gemini-2.0-flash-lite": (0.000075, 0.0003),
    "gemini-2.0-flash": (0.0001, 0.0004),
    "gemini-1.5-pro": (0.00125, 0.005),
    "gemini-1.5-flash-8b": (0.0000375, 0.00015),
    "gemini-1.5-flash": (0.000075, 0.0003),
    # --- DeepSeek (first-party API; chat/reasoner are now V4-flash aliases) ---
    "deepseek-v4-pro": (0.000435, 0.00087),
    "deepseek-v4-flash": (0.00014, 0.00028),
    "deepseek-v3.2": (0.00026, 0.00038),
    "deepseek-chat": (0.00014, 0.00028),
    "deepseek-reasoner": (0.00014, 0.00028),
    # --- DeepSeek open weights (hosted; representative) ---
    "deepseek-r1-distill-llama-70b": (0.00023, 0.00069),
    "deepseek-r1": (0.0005, 0.00215),
    "deepseek-v3": (0.0003, 0.00089),
    # --- Meta Llama (hosted; representative) ---
    "llama-4-maverick": (0.0002, 0.0006),
    "llama-4-scout": (0.0001, 0.0003),
    "llama-3.3-70b": (0.00012, 0.0003),
    "llama-3.2-3b": (0.00001, 0.00002),
    "llama-3.2-1b": (0.000005, 0.00001),
    "llama-3.1-405b": (0.0009, 0.0009),
    "llama-3.1-70b": (0.00023, 0.0004),
    "llama-3.1-8b": (0.00002, 0.00005),
    "llama-3-70b": (0.00023, 0.0004),
    "llama-3-8b": (0.00003, 0.00006),
    # --- Qwen (hosted; representative; both "qwen2.5"/"qwen-2.5" spellings) ---
    "qwen3-max": (0.0012, 0.006),
    "qwen-3-max": (0.0012, 0.006),
    "qwen2.5-coder": (0.00008, 0.00018),
    "qwen-2.5-coder": (0.00008, 0.00018),
    "qwen2.5-72b": (0.00036, 0.0004),
    "qwen-2.5-72b": (0.00036, 0.0004),
    "qwen2.5-7b": (0.00003, 0.00003),
    "qwen-2.5-7b": (0.00003, 0.00003),
    "qwen3-235b": (0.00009, 0.0001),
    "qwen-3-235b": (0.00009, 0.0001),
    "qwen3-32b": (0.0004, 0.0008),
    "qwen-3-32b": (0.0004, 0.0008),
    # --- Mistral ---
    "mistral-large": (0.002, 0.006),
    "mistral-small": (0.0002, 0.0006),
    "mistral-nemo": (0.00004, 0.0001),
    "mistral-7b": (0.00003, 0.00005),
    "ministral-8b": (0.0001, 0.0001),
    "ministral-3b": (0.00004, 0.00004),
    "codestral": (0.0003, 0.0009),
    "mixtral-8x22b": (0.0006, 0.0006),
    "mixtral-8x7b": (0.00024, 0.00024),
    # --- Google Gemma (hosted; representative) ---
    "gemma-4-31b": (0.00013, 0.00038),
    "gemma-3-27b": (0.00008, 0.00016),
    "gemma-2-27b": (0.00027, 0.00027),
    "gemma-2-9b": (0.00003, 0.00006),
    # --- xAI Grok ---
    "grok-3-mini": (0.0003, 0.0005),
    "grok-3": (0.003, 0.015),
    "grok-2": (0.002, 0.01),
    # --- Cohere ---
    "command-r-plus": (0.0025, 0.01),
    "command-r": (0.00015, 0.0006),
    "command-a": (0.0025, 0.01),
}

# runtime table = defaults plus any user overrides
_PRICES: dict[str, tuple[float, float]] = {k.lower(): v for k, v in PRICES.items()}


def register(model_key: str, input_per_1k: float, output_per_1k: float) -> None:
    """Add or override a model price at runtime."""
    _PRICES[model_key.lower()] = (float(input_per_1k), float(output_per_1k))


def load_overrides(path: str) -> int:
    """Load price overrides from a JSON file. Returns how many were applied."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    count = 0
    for key, value in data.items():
        register(key, value[0], value[1])
        count += 1
    return count


def _match(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    m = model.lower()
    for key in sorted(_PRICES, key=len, reverse=True):
        if key in m:
            return _PRICES[key]
    return None


# approximate context-window sizes (tokens), substring-matched like prices
CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-5.5-pro": 1050000, "gpt-5.5": 1050000, "gpt-5.4-pro": 1050000,
    "gpt-5.4-nano": 400000, "gpt-5.4-mini": 400000, "gpt-5.4": 1050000, "gpt-5.3-codex": 400000,
    "gpt-4.1": 1047576,
    "gpt-4o-mini": 128000, "chatgpt-4o-latest": 128000, "gpt-4o": 128000,
    "gpt-4-turbo": 128000, "gpt-4": 8192, "gpt-3.5-turbo": 16385,
    "o4-mini": 200000, "o3-mini": 200000, "o3": 200000,
    "o1-pro": 200000, "o1-mini": 128000, "o1": 200000,
    "gpt-oss-120b": 131072, "gpt-oss-20b": 131072,
    # Anthropic
    "claude-fable-5": 1000000,
    "claude-opus-4-8": 1000000, "claude-opus-4-7": 1000000, "claude-opus-4-6": 1000000,
    "claude-opus-4-5": 200000, "claude-sonnet-4-6": 1000000,
    "claude-3-5-sonnet": 200000, "claude-3-7-sonnet": 200000, "claude-3-5-haiku": 200000,
    "claude-3-sonnet": 200000, "claude-3-opus": 200000, "claude-3-haiku": 200000,
    "claude-sonnet-4": 200000, "claude-opus-4": 200000, "claude-haiku-4": 200000,
    # Google
    "gemini-3.5-flash": 1000000, "gemini-3.1-pro": 1000000,
    "gemini-3.1-flash-lite": 1000000, "gemini-3-flash": 1000000,
    "gemini-2.5-pro": 1048576, "gemini-2.5-flash-lite": 1048576, "gemini-2.5-flash": 1048576,
    "gemini-2.0-flash-lite": 1048576, "gemini-2.0-flash": 1048576,
    "gemini-1.5-pro": 2000000, "gemini-1.5-flash-8b": 1000000, "gemini-1.5-flash": 1000000,
    # DeepSeek
    "deepseek-v4-pro": 1000000, "deepseek-v4-flash": 1000000, "deepseek-v3.2": 160000,
    "deepseek-chat": 128000, "deepseek-reasoner": 128000,
    "deepseek-r1-distill-llama-70b": 131072, "deepseek-r1": 128000, "deepseek-v3": 128000,
    # Meta Llama
    "llama-4-maverick": 1000000, "llama-4-scout": 10000000,
    "llama-3.3-70b": 128000, "llama-3.2-3b": 128000, "llama-3.2-1b": 128000,
    "llama-3.1-405b": 128000, "llama-3.1-70b": 128000, "llama-3.1-8b": 128000,
    "llama-3-70b": 8192, "llama-3-8b": 8192,
    # Qwen
    "qwen3-max": 250000, "qwen-3-max": 250000,
    "qwen2.5-coder": 32768, "qwen-2.5-coder": 32768,
    "qwen2.5-72b": 131072, "qwen-2.5-72b": 131072, "qwen2.5-7b": 131072, "qwen-2.5-7b": 131072,
    "qwen3-235b": 262144, "qwen-3-235b": 262144, "qwen3-32b": 131072, "qwen-3-32b": 131072,
    # Mistral
    "mistral-large": 128000, "mistral-small": 32768, "mistral-nemo": 128000,
    "mistral-7b": 32768, "ministral-8b": 128000, "ministral-3b": 128000,
    "codestral": 256000, "mixtral-8x22b": 65536, "mixtral-8x7b": 32768,
    # Gemma
    "gemma-4-31b": 256000, "gemma-3-27b": 128000, "gemma-2-27b": 8192, "gemma-2-9b": 8192,
    # xAI Grok
    "grok-3-mini": 131072, "grok-3": 131072, "grok-2": 131072,
    # Cohere
    "command-r-plus": 128000, "command-r": 128000, "command-a": 256000,
}


def rates(model: str | None) -> tuple[float, float] | None:
    """(input_per_1k, output_per_1k) for a model, or None if unknown."""
    return _match(model)


def context_window(model: str | None) -> int | None:
    """The model's context window in tokens, or None if unknown."""
    if not model:
        return None
    m = model.lower()
    for key in sorted(CONTEXT_WINDOWS, key=len, reverse=True):
        if key in m:
            return CONTEXT_WINDOWS[key]
    return None


def cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated USD cost, or None if the model's pricing is unknown."""
    price = _match(model)
    if not price:
        return None
    return round(prompt_tokens / 1000 * price[0] + completion_tokens / 1000 * price[1], 6)


# Cache pricing as a multiple of the input rate, so the cost matches the bill
# when prompt caching is in play. read = cache-hit price, write = cache-creation
# price. Substring-matched on the model id (longest first), with a per-provider
# fallback. Models/providers that do not report cache usage never trigger these
# (their read/write token counts are zero), so cost is unchanged for them.
_CACHE_READ_MULT: dict[str, float] = {
    "claude": 0.1, "deepseek": 0.1, "gemini": 0.25,
    "gpt-4o": 0.5, "gpt-4-turbo": 0.5, "gpt-4": 0.5, "gpt-3.5": 0.5,
    "gpt-4.1": 0.25, "gpt-5": 0.25, "gpt-oss": 0.25, "o1": 0.25, "o3": 0.25, "o4": 0.25,
}
_CACHE_WRITE_MULT: dict[str, float] = {"claude": 1.25}
_PROVIDER_READ_DEFAULT: dict[str, float] = {"anthropic": 0.1, "openai": 0.5}


def _mult(table: dict[str, float], model: str | None, default: float) -> float:
    m = (model or "").lower()
    for key in sorted(table, key=len, reverse=True):
        if key in m:
            return table[key]
    return default


def effective_input_per_1k(model: str | None, provider: str, fresh_input: int | None,
                           cache_read: int | None, cache_write: int | None) -> float | None:
    """The blended input price per 1k tokens for this call, accounting for cache
    reads/writes. Use this to price reclaimable tokens so they reflect what those
    tokens actually cost (cheap when cached), keeping reclaimable <= spend."""
    rate = _match(model)
    if not rate:
        return None
    billed = (fresh_input or 0) + (cache_read or 0) + (cache_write or 0)
    if billed <= 0:
        return rate[0]
    input_cost = cost_cached(model, provider, fresh_input, cache_read, cache_write, 0)
    return (input_cost or 0.0) / billed * 1000


def cost_cached(model: str | None, provider: str, fresh_input: int | None,
                cache_read: int | None, cache_write: int | None,
                completion: int | None) -> float | None:
    """Cache-aware cost: fresh prompt tokens at full input rate, cache reads and
    writes at their multiples, completion at the output rate. Returns None if the
    model's pricing is unknown. Matches provider bills (e.g. Anthropic /usage)."""
    price = _match(model)
    if not price:
        return None
    inp, out = price
    read_mult = _mult(_CACHE_READ_MULT, model, _PROVIDER_READ_DEFAULT.get(provider, 0.25))
    write_mult = _mult(_CACHE_WRITE_MULT, model, 0.0)
    total = (
        (fresh_input or 0) * inp
        + (cache_read or 0) * inp * read_mult
        + (cache_write or 0) * inp * write_mult
        + (completion or 0) * out
    ) / 1000
    return round(total, 6)


# apply user overrides from the environment at import time
_override_path = os.environ.get("LLMPROF_PRICING")
if _override_path and os.path.exists(_override_path):
    try:
        load_overrides(_override_path)
    except (OSError, ValueError, KeyError, IndexError):
        pass
