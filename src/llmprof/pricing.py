"""Provider pricing table (USD per 1K tokens) for cost attribution.

Deliberately small and pluggable. Prices drift - keep this easy to update and
label costs as estimates. (input_per_1k, output_per_1k)
"""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "o1-mini": (0.0011, 0.0044),
    "o1": (0.015, 0.06),
    # Anthropic
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-7-sonnet": (0.003, 0.015),
    "claude-3-opus": (0.015, 0.075),
    "claude-3-haiku": (0.00025, 0.00125),
}


def _match(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    m = model.lower()
    # longest key first so gpt-4o-mini wins over gpt-4o, etc.
    for key in sorted(PRICES, key=len, reverse=True):
        if key in m:
            return PRICES[key]
    return None


def cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated USD cost, or None if the model's pricing is unknown."""
    price = _match(model)
    if not price:
        return None
    return round(prompt_tokens / 1000 * price[0] + completion_tokens / 1000 * price[1], 6)
