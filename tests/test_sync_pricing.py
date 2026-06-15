"""Tests for the pricing-snapshot generator (scripts/pricing/sync_pricing.py).

The transform is pure (LiteLLM dict in, snapshot dict out), so it is tested with a
small synthetic blob and no network. This proves the normalization (prefix and date
stripping, mode filter, per-token -> per-1k, dedupe) actually works before the
generated snapshot is trusted at runtime.
"""

from __future__ import annotations

import importlib.util
import pathlib

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pricing" / "sync_pricing.py"
_spec = importlib.util.spec_from_file_location("sync_pricing", _SCRIPT)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def test_normalize_key_strips_provider_region_and_date():
    assert sync.normalize_key("openrouter/anthropic/claude-3.5-sonnet") == "claude-3.5-sonnet"
    assert sync.normalize_key("us.anthropic.claude-opus-4-7-20250101-v1:0") == "claude-opus-4-7"
    assert sync.normalize_key("vertex_ai/gemini-2.5-pro") == "gemini-2.5-pro"
    assert sync.normalize_key("gpt-4o-2024-08-06") == "gpt-4o"
    assert sync.normalize_key("claude-3-5-sonnet@20240620") == "claude-3-5-sonnet"


def test_transform_filters_modes_converts_and_skips_generic():
    litellm = {
        "sample_spec": {"mode": "chat", "input_cost_per_token": 1.0},  # schema, ignored
        "gpt-4o-2024-08-06": {"mode": "chat", "input_cost_per_token": 2.5e-06,
                              "output_cost_per_token": 1e-05, "max_input_tokens": 128000,
                              "cache_read_input_token_cost": 1.25e-06},
        "text-embedding-3-small": {"mode": "embedding", "input_cost_per_token": 2e-08},
        "dall-e-3": {"mode": "image_generation"},
        "auto": {"mode": "chat", "input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
        "us.anthropic.claude-3-5-sonnet-20241022-v1:0": {
            "mode": "chat", "input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05},
    }
    out = sync.transform(litellm)
    # only the two real chat models survive; embedding/image/sample_spec/auto dropped
    assert set(out) == {"gpt-4o", "claude-3-5-sonnet"}
    # per-token -> per-1k
    assert out["gpt-4o"]["in"] == 0.0025 and out["gpt-4o"]["out"] == 0.01
    assert out["gpt-4o"]["ctx"] == 128000
    assert out["gpt-4o"]["cache_read"] == 0.00125
    # bedrock region + vendor + date all stripped down to the base key
    assert out["claude-3-5-sonnet"]["in"] == 0.003


def test_transform_dedupes_keeping_the_more_complete_entry():
    litellm = {
        "claude-3-haiku-20240307": {"mode": "chat", "input_cost_per_token": 2.5e-07,
                                    "output_cost_per_token": 1.25e-06},
        "anthropic/claude-3-haiku": {"mode": "chat", "input_cost_per_token": 2.5e-07,
                                     "output_cost_per_token": 1.25e-06,
                                     "max_input_tokens": 200000,
                                     "cache_read_input_token_cost": 3e-08},
    }
    out = sync.transform(litellm)
    assert set(out) == {"claude-3-haiku"}
    # the entry carrying ctx + cache_read wins the collision
    assert out["claude-3-haiku"]["ctx"] == 200000 and "cache_read" in out["claude-3-haiku"]


def test_build_snapshot_has_attribution_and_count():
    snap = sync.build_snapshot(
        {"gpt-4o": {"mode": "chat", "input_cost_per_token": 2.5e-06,
                    "output_cost_per_token": 1e-05}}, "2026-06-15")
    assert snap["_count"] == 1 and snap["_snapshot_date"] == "2026-06-15"
    assert "LiteLLM" in snap["_attribution"] and "Berri AI" in snap["_attribution"]
    assert snap["models"]["gpt-4o"]["in"] == 0.0025
