---
title: Providers & pricing
description: Supported providers, the built-in pricing table, and how to override prices.
---

llmprof prices every call from input/output dollars per token. It covers 1000+
model ids across the providers people actually use, is fully overridable, and
needs no network call: the data is bundled.

## Where the prices come from

Prices resolve from three tiers, highest precedence first:

1. **Your overrides** - anything you set via `LLMPROF_PRICING` (below).
2. **Curated families** - a small hand-maintained table that is authoritative for
   the headline and newest models (the latest Claude, GPT, and Gemini), so a brand
   new release is priced correctly even before the snapshot catches up.
3. **Bundled snapshot** - a vendored copy of
   [LiteLLM's pricing database](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)
   (MIT, Copyright (c) 2023 Berri AI), the ecosystem's provider-sourced table. It
   gives broad coverage for everything else: OpenAI, Anthropic, Gemini, DeepSeek,
   xAI Grok, Cohere, and hosted open weights (Llama, Qwen, Mistral / Mixtral,
   Gemma, GLM, Phi, and more).

The snapshot ships in the package and loads with no network call, so pricing works
fully offline. Refresh it by re-running the generator and committing the diff:

```bash
python scripts/pricing/sync_pricing.py --date YYYY-MM-DD
```

Within each tier, model ids match by exact id first, then the longest substring
key, so `gpt-4o-mini` is priced differently from `gpt-4o`, and a provider-prefixed
id like `meta-llama/Llama-3.1-405B-Instruct` still resolves.

## Unknown models degrade gracefully

If a model id matches nothing in the table, the call still shows its token
breakdown - only the dollar figure is omitted. Accuracy of the displayed cost
matters more than coverage, so prices that could not be verified are left out
rather than guessed.

## Hosted open-weight prices are representative

The same open model (say Llama 3.3 70B) costs different amounts on DeepInfra vs
Fireworks vs Cerebras vs the first-party API. The curated and snapshot values are
representative of one provider; pin exact numbers for yours with an override.

## Overriding prices

Point `LLMPROF_PRICING` at a JSON file of `{"model": [input_per_1k, output_per_1k]}`:

```json
{
  "my-finetuned-gpt-4o": [0.003, 0.012],
  "llama-3.3-70b": [0.00023, 0.0004]
}
```

```bash
LLMPROF_PRICING=./prices.json llmprof up
```

Overrides are the highest-precedence tier, so they win over both the curated and
bundled tables. You only need to list what you want to change or add.
