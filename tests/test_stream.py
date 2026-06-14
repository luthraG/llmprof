"""Direct unit tests for the SSE scrapers (provider stream parsing)."""

from llmprof.proxy import _scrape_anthropic, _scrape_openai


def test_scrape_openai_collects_text_deltas():
    state = {"text": [], "input": None, "output": None}
    _scrape_openai(b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n', state)
    _scrape_openai(b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n', state)
    _scrape_openai(b"data: [DONE]\n\n", state)
    assert "".join(state["text"]) == "Hello"


def test_scrape_openai_reads_usage_when_present():
    state = {"text": [], "input": None, "output": None}
    _scrape_openai(
        b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":11,"completion_tokens":4}}\n\n',
        state,
    )
    assert state["input"] == 11
    assert state["output"] == 4


def test_scrape_anthropic_reads_exact_usage_and_text():
    state = {"text": [], "input": None, "output": None}
    _scrape_anthropic(
        b'data: {"type":"message_start","message":'
        b'{"usage":{"input_tokens":40,"output_tokens":1}}}\n\n',
        state,
    )
    _scrape_anthropic(
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n',
        state,
    )
    _scrape_anthropic(
        b'data: {"type":"message_delta","delta":{},"usage":{"output_tokens":9}}\n\n', state
    )
    assert state["input"] == 40
    assert state["output"] == 9
    assert "".join(state["text"]) == "Hi"


def test_scrape_ignores_malformed_lines():
    state = {"text": [], "input": None, "output": None}
    _scrape_openai(b"data: not-json\n\nrandom noise\n", state)
    _scrape_anthropic(b"event: ping\ndata: {bad}\n\n", state)
    assert state["text"] == []
    assert state["input"] is None
