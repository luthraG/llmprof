from llmprof import pricing, tokens


def test_count_tokens_nonzero():
    assert tokens.count_tokens("hello world", "gpt-4o") > 0
    assert tokens.count_tokens("", "gpt-4o") == 0


def test_attribute_breaks_into_components():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
    ]
    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web for a query string",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
    ]
    result = tokens.attribute(messages, tool_defs, "gpt-4o")
    comps = result["components"]
    assert "system prompt" in comps
    assert "user input" in comps
    assert "tool schemas" in comps
    assert comps["tool schemas"] > 0
    # components (+ priming) should sum to the reported total
    assert result["total"] == sum(comps.values()) + 3
    assert result["approximate"] is True


def test_pricing_known_and_unknown():
    c = pricing.cost("gpt-4o", 1000, 1000)
    assert c == round(0.0025 + 0.01, 6)
    assert pricing.cost("gpt-4o-mini", 1000, 0) == 0.00015  # longest-match wins
    assert pricing.cost("some-unknown-model", 1000, 1000) is None
