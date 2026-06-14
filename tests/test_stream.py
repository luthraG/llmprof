"""Direct unit tests for the SSE scrapers (provider stream parsing)."""

from llmprof.proxy import _scrape_anthropic, _scrape_openai, _scrape_responses

_BLANK = {"text": [], "fresh": None, "read": None, "write": None,
          "output": None, "prompt_total": None}


def _state():
    return dict(_BLANK, text=[])


def test_scrape_openai_collects_text_deltas():
    state = _state()
    _scrape_openai(b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n', state)
    _scrape_openai(b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n', state)
    _scrape_openai(b"data: [DONE]\n\n", state)
    assert "".join(state["text"]) == "Hello"


def test_scrape_openai_reads_usage_when_present():
    state = _state()
    _scrape_openai(
        b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":11,"completion_tokens":4,'
        b'"prompt_tokens_details":{"cached_tokens":8}}}\n\n',
        state,
    )
    assert state["prompt_total"] == 11
    assert state["read"] == 8 and state["fresh"] == 3  # full prompt minus cached
    assert state["output"] == 4


def test_scrape_anthropic_reads_exact_usage_and_text():
    state = _state()
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
    assert state["fresh"] == 40 and state["prompt_total"] == 40
    assert state["output"] == 9
    assert "".join(state["text"]) == "Hi"


def test_scrape_anthropic_reads_cache_tokens():
    state = _state()
    _scrape_anthropic(
        b'data: {"type":"message_start","message":{"usage":'
        b'{"input_tokens":700,"cache_read_input_tokens":85700,'
        b'"cache_creation_input_tokens":7600,"output_tokens":1}}}\n\n',
        state,
    )
    assert state["fresh"] == 700
    assert state["read"] == 85700
    assert state["write"] == 7600
    assert state["prompt_total"] == 700 + 85700 + 7600


def test_scrape_responses_reads_text_usage_and_tools():
    state = _state()
    _scrape_responses(b'data: {"type":"response.output_text.delta","delta":"Hel"}\n\n', state)
    _scrape_responses(b'data: {"type":"response.output_text.delta","delta":"lo"}\n\n', state)
    _scrape_responses(
        b'data: {"type":"response.output_item.added","item":'
        b'{"type":"function_call","name":"search"}}\n\n', state)
    _scrape_responses(
        b'data: {"type":"response.completed","response":{"usage":'
        b'{"input_tokens":1000,"output_tokens":9,"input_tokens_details":{"cached_tokens":600}}}}\n\n',
        state)
    assert "".join(state["text"]) == "Hello"
    assert state["prompt_total"] == 1000 and state["read"] == 600 and state["fresh"] == 400
    assert state["output"] == 9
    assert "search" in state.get("tools", [])


def test_scrape_ignores_malformed_lines():
    state = _state()
    _scrape_openai(b"data: not-json\n\nrandom noise\n", state)
    _scrape_anthropic(b"event: ping\ndata: {bad}\n\n", state)
    assert state["text"] == []
    assert state["fresh"] is None
