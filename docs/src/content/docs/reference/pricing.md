---
title: Providers & pricing
description: Supported providers, the built-in pricing table, and how to override prices.
---

llmprof prices every call from a built-in table of input/output dollars per
token. It covers 100+ model ids across the providers people actually use, and is
fully overridable.

## Supported providers

Any OpenAI- or Anthropic-compatible endpoint works for *capture*. For *pricing*,
the table includes:

- **OpenAI** - GPT-4o / 4.1 families, the o-series, the latest GPT-5 family,
  gpt-oss.
- **Anthropic** - Claude 3.x and 4.x (including the newer, cheaper Opus tiers)
  and Fable.
- **Google Gemini** - 1.5 / 2.0 / 2.5 / 3.x families.
- **DeepSeek** - chat / reasoner and the V4 family.
- **Hosted open weights** (DeepInfra, Fireworks, Cerebras, Together) - Llama
  3.x/4, Qwen 2.5/3, Mistral / Mixtral, Gemma, DeepSeek V3/R1, and more.
- **xAI Grok** and **Cohere Command**.

Model ids are matched by substring, longest match first, so
`gpt-4o-mini` is priced differently from `gpt-4o`, and a provider-prefixed id
like `meta-llama/Llama-3.1-405B-Instruct` still resolves.

## Unknown models degrade gracefully

If a model id matches nothing in the table, the call still shows its token
breakdown - only the dollar figure is omitted. Accuracy of the displayed cost
matters more than coverage, so prices that could not be verified are left out
rather than guessed.

## Hosted open-weight prices are representative

The same open model (say Llama 3.3 70B) costs different amounts on DeepInfra vs
Fireworks vs Cerebras. The built-in values are representative; pin exact numbers
for your provider with an override.

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

Overrides are merged on top of the built-in table at startup, so you only need to
list what you want to change or add.
