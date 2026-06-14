---
title: Context flame graph
description: See one request's prompt tokens broken down by component, with per-tool drill-down.
---

The flame graph is the core view: it shows what a single request's context window
is actually made of, so the waste is obvious at a glance.

![A context flame graph for a gpt-4o-mini call. A reclaimable strip sits at the top, the flame graph shows tool schemas, history, and user input with individual tools as drill-down children, and the optimization panel lists findings.](../../../assets/screenshots/flame-graph.png)

## Reading it

- The top row is the whole `context`. Each row below splits it into components
  (`system prompt`, `tool schemas`, `history (assistant)`, `user input`, ...),
  sized by token count, heaviest first.
- `tool schemas` expands into one child per tool, so you can see which specific
  tool definitions are eating tokens.
- Hover any frame for its exact tokens, share of context, and input cost. Click a
  frame to zoom in; use the breadcrumb to zoom back out.

## The header

Right under the model name, a strip calls out **reclaimable on this call** -
tokens and dollars - and what percent of the call's cost that is. Below it, the
key stats (prompt / completion / total / cost) and a context-window gauge.

## Optimization findings

The optimization panel lists concrete waste for this call (unused tools,
duplicated content, oversized schemas, uncached prefix), each with a per-call
saving where it can be quantified. The full logic is in
[The waste detector](../../concepts/waste-detector/).

## Tip: find the worst offenders fast

In the calls list, sort by **needs attention** to push the calls with the most
findings to the top, or by **most $** to start with the priciest.
