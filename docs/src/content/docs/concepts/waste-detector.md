---
title: The waste detector
description: How llmprof flags wasteful context and turns it into a reclaimable-dollars number.
---

Every captured call is run through a waste detector. It produces concrete
findings, each carrying how much it could reclaim, and rolls them up into a
single headline: how much of your spend is reclaimable.

## Two kinds of reclaimable

llmprof is deliberate about not overstating savings. Findings fall into two
tiers:

- **Removable tokens** - tokens you could literally drop from the call:
  - *Duplicated content*: the same block (a RAG chunk, an instruction) appearing
    more than once in the context. The extra copies are reclaimable.
  - *Unused tool schemas*: tools defined on the request that the model never
    called. Their schemas are dead weight for that call.
- **Recurring dollars** - savings from a change in how you call, not from
  removing tokens:
  - *Uncached stable prefix*: a system prompt + tool schemas that repeat on every
    call. Prompt caching can cut about 90% off them after the first call.

The per-call **reclaimable** number sums the dollars across both tiers; the token
figure counts only the removable tokens.

## Advisory findings

Some findings flag a smell without claiming a precise reclaimable amount, because
the right fix depends on your app:

- Tool schemas are a large share of the context (trim descriptions, lazy-load).
- History and tool results dominate (summarize or truncate older turns).
- A very large system prompt riding every call.

## The monthly headline

On the [Trends](../../features/trends/) view, llmprof sums reclaimable spend
across all recorded calls and projects it to a month using your observed call
rate:

> **RECLAIMABLE / MO  $X**  ·  ~N% of spend  ·  projected from M calls/mo

This is the number to act on. It is an estimate (it scales observed local
traffic to 30 days), and it is honest about uncertainty: brand-new and hosted
open-model prices drift, and findings that cannot be quantified are kept advisory
rather than padded into the total.

## Where it runs

Detection runs once, off the hot path, when a call is recorded - so the dashboard
and the [Python SDK](../../sdk/python/) share exactly one implementation. SDK
traces get the same findings as proxied ones.
