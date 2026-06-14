import types

import pytest

import llmprof
from llmprof.store import Store


def test_profile_records_tagged_components(tmp_path):
    db = str(tmp_path / "sdk.db")
    with llmprof.profile(model="gpt-4o", db_path=db) as p:
        p.add("system prompt", "You are a helpful assistant. " * 20)
        p.add("rag_chunk", "retrieved document body " * 40, name="kb#42")
        p.add("tool", {"name": "search", "description": "search the web"},
              name="search", called=True)
        p.usage({"prompt_tokens": 1234, "completion_tokens": 56})

    rows = Store(db).recent(10)
    assert len(rows) == 1
    detail = Store(db).get(rows[0]["id"])
    assert detail["prompt_tokens"] == 1234 and detail["completion_tokens"] == 56
    assert detail["cost_usd"] > 0
    names = [c["name"] for c in detail["detail"]["children"]]
    assert "system prompt" in names and "rag chunks" in names and "tool schemas" in names
    rag = next(c for c in detail["detail"]["children"] if c["name"] == "rag chunks")
    assert any(c["name"] == "kb#42" for c in rag["children"])
    assert detail["analysis"] and "findings" in detail["analysis"]


def test_usage_from_object_and_explicit(tmp_path):
    db = str(tmp_path / "u.db")
    usage_obj = types.SimpleNamespace(prompt_tokens=10, completion_tokens=2)
    with llmprof.profile(model="gpt-4o", db_path=db) as p:
        p.add("system prompt", "hi")
        p.usage(usage_obj)
    assert Store(db).recent(1)[0]["prompt_tokens"] == 10

    db2 = str(tmp_path / "u2.db")
    with llmprof.profile(model="gpt-4o", db_path=db2) as p:
        p.add("system prompt", "hi")
        p.usage(prompt_tokens=99, completion_tokens=1, cached_tokens=40)
    row = Store(db2).get(Store(db2).recent(1)[0]["id"])
    assert row["prompt_tokens"] == 99 and row["cached_tokens"] == 40


def test_decorator_and_module_helpers(tmp_path):
    db = str(tmp_path / "deco.db")

    @llmprof.profiled(model="gpt-4o", db_path=db)
    def answer(q):
        llmprof.add("system prompt", "You answer questions. " * 10)
        llmprof.add("user input", q)
        llmprof.usage(prompt_tokens=42, completion_tokens=7)
        return "ok"

    assert answer("why?") == "ok"
    rows = Store(db).recent(10)
    assert len(rows) == 1 and rows[0]["prompt_tokens"] == 42


def test_falls_back_to_attributed_tokens_without_usage(tmp_path):
    db = str(tmp_path / "noUsage.db")
    with llmprof.profile(model="gpt-4o", db_path=db) as p:
        n = p.add("system prompt", "count me " * 10)
    row = Store(db).recent(1)[0]
    assert row["prompt_tokens"] == n  # attributed token sum when usage not given


def test_helpers_outside_context_raise():
    with pytest.raises(RuntimeError):
        llmprof.add("system prompt", "x")
    with pytest.raises(RuntimeError):
        llmprof.usage(prompt_tokens=1)
