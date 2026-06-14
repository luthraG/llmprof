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

# (input_per_1k, output_per_1k). Substring-matched against the model id, longest
# key first, so "gpt-4o-mini" beats "gpt-4o". Estimates as of early 2026.
PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4.1-nano": (0.0001, 0.0004),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "o4-mini": (0.0011, 0.0044),
    "o3-mini": (0.0011, 0.0044),
    "o3": (0.002, 0.008),
    "o1-mini": (0.0011, 0.0044),
    "o1": (0.015, 0.06),
    # Anthropic
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-7-sonnet": (0.003, 0.015),
    "claude-haiku-4": (0.001, 0.005),
    "claude-sonnet-4": (0.003, 0.015),
    "claude-opus-4": (0.015, 0.075),
    "claude-3-opus": (0.015, 0.075),
    "claude-3-haiku": (0.00025, 0.00125),
    # Google (estimates; commonly used via the OpenAI-compatible endpoint)
    "gemini-2.0-flash": (0.0001, 0.0004),
    "gemini-1.5-flash": (0.000075, 0.0003),
    "gemini-1.5-pro": (0.00125, 0.005),
    # Others (estimates)
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-reasoner": (0.00055, 0.00219),
    "mistral-large": (0.002, 0.006),
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
    "gpt-4.1": 1047576,
    "gpt-4o-mini": 128000, "gpt-4o": 128000, "gpt-4-turbo": 128000, "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    "o4-mini": 200000, "o3-mini": 200000, "o3": 200000, "o1-mini": 128000, "o1": 200000,
    "claude-3-5-sonnet": 200000, "claude-3-7-sonnet": 200000, "claude-3-5-haiku": 200000,
    "claude-3-opus": 200000, "claude-3-haiku": 200000,
    "claude-sonnet-4": 200000, "claude-opus-4": 200000, "claude-haiku-4": 200000,
    "gemini-1.5-pro": 2000000, "gemini-1.5-flash": 1000000, "gemini-2.0-flash": 1000000,
    "deepseek-chat": 64000, "deepseek-reasoner": 64000, "mistral-large": 128000,
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


# apply user overrides from the environment at import time
_override_path = os.environ.get("LLMPROF_PRICING")
if _override_path and os.path.exists(_override_path):
    try:
        load_overrides(_override_path)
    except (OSError, ValueError, KeyError, IndexError):
        pass
