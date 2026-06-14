from llmprof import analyze


def _tree(system=600, tools=(("a", 200), ("b", 200))):
    children = [{"name": "system prompt", "tokens": system, "children": []}]
    if tools:
        children.append({
            "name": "tool schemas", "tokens": sum(t for _, t in tools),
            "children": [{"name": n, "tokens": t, "children": []} for n, t in tools],
        })
    total = sum(c["tokens"] for c in children)
    return {"name": "context", "tokens": total, "children": children}


def test_duplicate_tokens_counts_extra_copies():
    chunk = "retrieved context paragraph. " * 20  # well over the min length
    assert analyze.duplicate_tokens([chunk, chunk, "unique short"], "gpt-4o") > 0
    # a single copy is not a duplicate
    assert analyze.duplicate_tokens([chunk], "gpt-4o") == 0
    # short blocks are ignored even when repeated
    assert analyze.duplicate_tokens(["tiny", "tiny"], "gpt-4o") == 0


def test_analyze_flags_unused_tools_with_reclaimable():
    res = analyze.analyze(_tree(), texts=[], input_per_1k=0.0025, called_tools=["a"])
    unused = next(f for f in res["findings"] if "were not called" in f["title"])
    assert unused["reclaimable_tokens"] == 200
    assert unused["save_usd"] == round(200 / 1000 * 0.0025, 6)
    assert res["reclaimable_tokens"] == 200
    assert res["reclaimable_usd"] > 0


def test_analyze_flags_duplicates():
    chunk = "the same retrieved document chunk repeated verbatim. " * 12
    res = analyze.analyze(_tree(tools=None), texts=[chunk, chunk],
                          input_per_1k=0.0025, called_tools=None)
    dup = next(f for f in res["findings"] if "Duplicated content" in f["title"])
    assert dup["reclaimable_tokens"] > 0
    assert res["reclaimable_usd"] > 0


def test_analyze_caching_tip_for_uncached_prefix():
    tree = _tree(system=1200, tools=(("a", 300),))  # prefix 1500 >= 1024, not cached
    res = analyze.analyze(tree, texts=[], input_per_1k=0.0025,
                          cached_tokens=None, called_tools=["a"])
    tip = next(f for f in res["findings"] if "not cached" in f["title"])
    assert tip["save_usd"] > 0  # recurring caching savings, shown inline
    assert tip["reclaimable_tokens"] == 0  # caching is not removable tokens
    # ...and the caching tip must NOT inflate the headline reclaimable (no double
    # counting against the prefix). With no duplicates/unused tools, headline is 0.
    assert res["reclaimable_tokens"] == 0
    assert res["reclaimable_usd"] == 0.0


def test_analyze_does_not_suggest_caching_when_already_caching():
    # a big stable prefix that WOULD trigger the caching tip if uncached...
    tree = _tree(system=1200, tools=(("a", 300),))
    # ...but this call is a cache write (caching is in use), so do not suggest it
    res = analyze.analyze(tree, texts=[], input_per_1k=0.0025, cached_tokens=0, cache_write=1000)
    titles = [f["title"] for f in res["findings"]]
    assert not any("not cached" in t for t in titles)
    assert any("caching is active" in t for t in titles)


def test_analyze_clean_context():
    tree = {"name": "context", "tokens": 120,
            "children": [{"name": "system prompt", "tokens": 60, "children": []},
                         {"name": "user input", "tokens": 60, "children": []}]}
    res = analyze.analyze(tree, texts=["short user message"], input_per_1k=0.0025)
    assert [f["severity"] for f in res["findings"]] == ["ok"]
    assert res["reclaimable_usd"] == 0.0
    assert res["reclaimable_tokens"] == 0
