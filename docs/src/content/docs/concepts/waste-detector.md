---
title: The waste detector
description: How llmprof flags wasteful context and turns it into a reclaimable-dollars number.
---

Every captured call is run through a waste detector. It produces concrete
findings, each carrying how much it could reclaim, and rolls them up into a
single headline: how much of your spend is reclaimable.

## What counts as reclaimable

llmprof is deliberate about not overstating savings. It only counts spend you can
actually recover, priced at the call's cache-aware rate so the figure never
exceeds what you paid:

- *Duplicated content*: the same block (a RAG chunk, an instruction) appearing
  more than once in the context. The extra copies are removable tokens.
- *Uncached stable prefix*: a system prompt + tool schemas that repeat on every
  call. Prompt caching can cut about 90% off them after the first call (a
  recurring saving, not removed tokens).
- *Tools never used across the window*: schemas for tools you ship on every call
  but that are never invoked across all captured calls. This is gated - a tool
  unused on a single call is not waste (an agent needs its full toolset across a
  run), so llmprof waits until it has seen enough calls before claiming it.

## Advisory findings

Some findings flag a smell without claiming a precise reclaimable amount, because
the right fix depends on your app, or because acting on them is not always
possible:

- Tools not called on a given request (informational; only tools never used
  across the whole run are counted as reclaimable).
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
