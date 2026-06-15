import json

from llmprof import pricing


def test_known_models_priced():
    assert pricing.cost("gpt-4o", 1000, 1000) == round(0.0025 + 0.01, 6)
    assert pricing.cost("claude-3-5-sonnet", 1000, 1000) == round(0.003 + 0.015, 6)
    assert pricing.cost("gpt-4.1", 1000, 0) == 0.002


def test_longest_match_wins():
    # "gpt-4o-mini" must not be priced as "gpt-4o"
    assert pricing.cost("gpt-4o-mini", 1000, 0) == 0.00015
    assert pricing.cost("gpt-4.1-mini", 1000, 0) == 0.0004


def test_unknown_model_returns_none():
    assert pricing.cost("totally-made-up-model", 1000, 1000) is None


def test_runtime_register_override():
    pricing.register("test-only-model-xyz", 0.5, 1.0)
    assert pricing.cost("provider/test-only-model-xyz", 1000, 1000) == round(0.5 + 1.0, 6)


def test_context_window_lookup():
    assert pricing.context_window("gpt-4o") == 128000
    assert pricing.context_window("claude-3-5-sonnet-20241022") == 200000
    assert pricing.context_window("gpt-4o-mini") == 128000  # longest match
    assert pricing.context_window("totally-unknown") is None


def test_load_overrides_from_json(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"acme-llm-7b": [0.01, 0.02]}))
    applied = pricing.load_overrides(str(path))
    assert applied == 1
    assert pricing.cost("acme-llm-7b", 1000, 1000) == round(0.01 + 0.02, 6)


# --- bundled LiteLLM snapshot (broad coverage) -------------------------------

def test_bundled_snapshot_loaded_and_large():
    # the vendored snapshot gives us 10x+ the hand-curated coverage
    assert len(pricing._BUNDLED) > 800


def test_bundled_covers_tail_models_curated_never_listed():
    # models that are not in the curated table now get a price from the bundle,
    # instead of None. Use ids unlikely to ever be hand-curated.
    for model in ("phi-4", "glm-4.6", "command-a-vision"):
        assert pricing.rates(model) is not None, model
        rate = pricing.rates(model)
        assert rate[0] >= 0 and rate[1] >= 0


def test_curated_tier_wins_over_bundle():
    # the bundle also lists gpt-4o / claude-opus-4-8, but the curated rate is the
    # authoritative one and must be returned unchanged.
    assert pricing.rates("gpt-4o") == (0.0025, 0.01)
    assert pricing.rates("claude-opus-4-8") == (0.005, 0.025)


def test_override_tier_wins_over_curated_and_bundle():
    pricing.register("gpt-4o", 9.0, 9.0)
    try:
        assert pricing.rates("gpt-4o") == (9.0, 9.0)
    finally:
        pricing._OVERRIDES.pop("gpt-4o", None)
        pricing._resort("overrides", pricing._OVERRIDES)
    assert pricing.rates("gpt-4o") == (0.0025, 0.01)  # restored


def test_bundle_fills_context_windows_for_tail():
    # curated windows still win; the bundle supplies the rest
    assert pricing.context_window("gpt-4o") == 128000          # curated
    assert pricing.context_window("kimi-k2") is not None       # bundled only


def test_bundled_data_integrity():
    for key, (inp, out) in pricing._BUNDLED.items():
        assert inp == inp and out == out          # not NaN
        assert inp >= 0 and out >= 0 and inp < 100 and out < 100, key
