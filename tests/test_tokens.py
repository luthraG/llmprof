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


def test_message_fingerprint_prefix_chain():
    """Turn N's fingerprint is a strict prefix of turn N+1's, for both providers."""
    t1 = {"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "q1"}]}
    t2 = {"messages": t1["messages"] + [{"role": "assistant", "content": "a1"},
                                        {"role": "user", "content": "q2"}]}
    fp1 = tokens.message_fingerprint(t1, "openai")
    fp2 = tokens.message_fingerprint(t2, "openai")
    assert len(fp1) == 2 and len(fp2) == 4
    assert fp2[: len(fp1)] == fp1  # earlier turn is a prefix of the later one

    a1 = {"system": "sys", "messages": [{"role": "user", "content": "hi"}]}
    a2 = {"system": "sys", "messages": a1["messages"] + [{"role": "assistant", "content": "yo"},
                                                         {"role": "user", "content": "more"}]}
    fa1 = tokens.message_fingerprint(a1, "anthropic")
    fa2 = tokens.message_fingerprint(a2, "anthropic")
    assert fa1[0].startswith("s:")  # system is part of the chain root for Anthropic
    assert fa2[: len(fa1)] == fa1
    # an unrelated conversation does not share the prefix
    other = tokens.message_fingerprint({"messages": [{"role": "user", "content": "zzz"}]}, "openai")
    assert other != fp1[: len(other)]


def test_route_label_groups_by_template():
    base = {
        "messages": [
            {"role": "system", "content": "You are an SRE assistant for incidents"},
            {"role": "user", "content": "what broke"},
        ],
        "tools": [{"type": "function", "function": {"name": "a"}},
                  {"type": "function", "function": {"name": "b"}}],
    }
    label = tokens.route_label(base, "openai")
    assert "SRE assistant" in label and "+2 tools" in label
    # same template, different user message -> same route key
    other = {"messages": [base["messages"][0], {"role": "user", "content": "different q"}],
             "tools": base["tools"]}
    assert tokens.route_label(other, "openai") == label
    # no system prompt
    assert tokens.route_label({"messages": [{"role": "user", "content": "hi"}]},
                              "openai").startswith("(no system prompt)")
    # anthropic reads system from its own field
    assert "Be concise" in tokens.route_label({"system": "Be concise", "messages": []}, "anthropic")


def test_pricing_known_and_unknown():
    c = pricing.cost("gpt-4o", 1000, 1000)
    assert c == round(0.0025 + 0.01, 6)
    assert pricing.cost("gpt-4o-mini", 1000, 0) == 0.00015  # longest-match wins
    assert pricing.cost("claude-3-5-sonnet", 1000, 1000) == round(0.003 + 0.015, 6)
    assert pricing.cost("some-unknown-model", 1000, 1000) is None


def test_pricing_expanded_models_and_longest_match():
    # newer Claude dotted subversions fall back to the family tier price
    assert pricing.rates("claude-sonnet-4-6-20251015") == (0.003, 0.015)
    assert pricing.rates("claude-opus-4-1") == (0.015, 0.075)
    # provider-prefixed open-weight ids match by substring, case-insensitively
    assert pricing.rates("meta-llama/Llama-3.1-405B-Instruct") == (0.0009, 0.0009)
    assert pricing.rates("Qwen/Qwen2.5-72B-Instruct") == (0.00036, 0.0004)
    assert pricing.rates("cerebras/qwen-3-32b") == (0.0004, 0.0008)
    # longest-match disambiguation
    assert pricing.rates("gemini-2.5-flash-lite-preview") == (0.0001, 0.0004)
    assert pricing.rates("gemini-2.5-flash") == (0.0003, 0.0025)
    assert pricing.rates("deepseek-r1-distill-llama-70b") == (0.00023, 0.00069)
    assert pricing.rates("deepseek-r1") == (0.0005, 0.00215)
    assert pricing.rates("mixtral-8x22b-instruct") == (0.0006, 0.0006)
    assert pricing.rates("grok-3-mini") == (0.0003, 0.0005)
    # context windows came along for the new families
    assert pricing.context_window("gemini-2.5-pro") == 1048576
    assert pricing.context_window("deepseek-chat") == 128000
    assert pricing.context_window("llama-3.3-70b-instruct") == 128000


def test_pricing_latest_models():
    # newest 2026 flagships across the providers in use
    assert pricing.rates("gpt-5.5") == (0.005, 0.03)
    assert pricing.rates("gpt-5.4-mini") == (0.00075, 0.0045)  # longer key beats gpt-5.4
    assert pricing.rates("claude-fable-5") == (0.01, 0.05)
    # the cheaper Opus 4.5+ tier must win over the old claude-opus-4 (15/75) family key
    assert pricing.rates("claude-opus-4-8-20251101") == (0.005, 0.025)
    assert pricing.rates("claude-opus-4-1") == (0.015, 0.075)  # 4.0/4.1 still the old tier
    assert pricing.rates("gemini-3.5-flash") == (0.0015, 0.009)
    assert pricing.rates("gemini-3.1-pro-preview") == (0.002, 0.012)
    # DeepSeek chat/reasoner are now V4-flash aliases at the lower price
    assert pricing.rates("deepseek-chat") == (0.00014, 0.00028)
    assert pricing.rates("deepseek-v4-pro") == (0.000435, 0.00087)
    assert pricing.rates("qwen3-max") == (0.0012, 0.006)
    assert pricing.context_window("gpt-5.5") == 1050000
    assert pricing.context_window("claude-opus-4-8") == 1000000
