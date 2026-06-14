from llmprof import pricing, tokens


def test_count_tokens_nonzero():
    assert tokens.count_tokens("hello world", "gpt-4o") > 0
    assert tokens.count_tokens("", "gpt-4o") == 0


def test_attribute_openai_breaks_into_components():
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
    result = tokens.attribute_openai(messages, tool_defs, "gpt-4o")
    comps = result["components"]
    assert "system prompt" in comps
    assert "user input" in comps
    assert "tool schemas" in comps
    assert comps["tool schemas"] > 0
    # components (+ priming) should sum to the reported total
    assert result["total"] == sum(comps.values()) + 3
    assert result["approximate"] is True
    # back-compat alias
    assert tokens.attribute is tokens.attribute_openai


def test_attribute_anthropic_breaks_into_components():
    system = "You are a precise assistant."
    messages = [
        {"role": "user", "content": "Search for the capital of France."},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me look that up."},
                {"type": "tool_use", "name": "search", "input": {"q": "capital of France"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "Paris is the capital."}
            ],
        },
    ]
    tool_defs = [
        {
            "name": "search",
            "description": "Search the web",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]
    result = tokens.attribute_anthropic(system, messages, tool_defs, "claude-3-5-sonnet")
    comps = result["components"]
    assert "system prompt" in comps
    assert "user input" in comps
    assert "history (assistant)" in comps
    assert "tool calls" in comps
    assert "tool results" in comps
    assert "tool schemas" in comps
    assert result["total"] == sum(comps.values())
    assert result["total"] > 0


def test_openai_tree_has_per_tool_children():
    tool_defs = [
        {"type": "function", "function": {"name": "alpha", "description": "x", "parameters": {}}},
        {"type": "function", "function": {"name": "beta", "description": "y", "parameters": {}}},
    ]
    result = tokens.attribute_openai([{"role": "user", "content": "hi"}], tool_defs, "gpt-4o")
    assert result["tree"]["name"] == "context"
    ts = next(n for n in result["tree"]["children"] if n["name"] == "tool schemas")
    assert {c["name"] for c in ts["children"]} == {"alpha", "beta"}
    assert ts["tokens"] == sum(c["tokens"] for c in ts["children"])


def test_anthropic_system_as_blocks():
    result = tokens.attribute_anthropic(
        system=[{"type": "text", "text": "Be terse."}],
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result["components"]["system prompt"] > 0


def test_openai_multimodal_content():
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "describe this image please"},
            {"type": "image_url", "image_url": {"url": "..."}},
        ]}
    ]
    result = tokens.attribute_openai(messages, None, "gpt-4o")
    assert result["components"]["user input"] > 0


def test_anthropic_tool_result_as_blocks():
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x",
             "content": [{"type": "text", "text": "the search returned several results"}]},
        ]}
    ]
    result = tokens.attribute_anthropic(None, messages, None, "claude-3-5-sonnet")
    assert result["components"].get("tool results", 0) > 0


def test_pricing_known_and_unknown():
    c = pricing.cost("gpt-4o", 1000, 1000)
    assert c == round(0.0025 + 0.01, 6)
    assert pricing.cost("gpt-4o-mini", 1000, 0) == 0.00015  # longest-match wins
    assert pricing.cost("claude-3-5-sonnet", 1000, 1000) == round(0.003 + 0.015, 6)
    assert pricing.cost("some-unknown-model", 1000, 1000) is None
