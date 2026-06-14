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
