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


def test_analyze_flags_unused_tools_as_informational_not_reclaimable():
    # an agent needs its full toolset across a run, so tools unused on ONE call
    # are not per-call reclaimable; the finding is informational only.
    res = analyze.analyze(_tree(), texts=[], input_per_1k=0.0025, called_tools=["a"])
    unused = next(f for f in res["findings"] if "were not called" in f["title"])
    assert unused["reclaimable_tokens"] == 0
    assert unused["save_usd"] is None
    assert res["reclaimable_tokens"] == 0  # not counted toward the headline
    assert res["reclaimable_usd"] == 0.0


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
    assert tip["save_usd"] > 0  # recurring caching savings
    assert tip["reclaimable_tokens"] == 0  # caching is not removable tokens
    # an uncached stable prefix IS reclaimable (via caching), so it counts toward
    # the per-call reclaimable dollars, but adds no removable tokens.
    assert res["reclaimable_tokens"] == 0
    assert res["reclaimable_usd"] == tip["save_usd"]


def test_analyze_does_not_suggest_caching_when_already_caching():
    # a big stable prefix that WOULD trigger the caching tip if uncached...
    tree = _tree(system=1200, tools=(("a", 300),))
    # ...but this call is a cache write (caching is in use), so do not suggest it
    res = analyze.analyze(tree, texts=[], input_per_1k=0.0025, cached_tokens=0, cache_write=1000)
    titles = [f["title"] for f in res["findings"]]
    assert not any("not cached" in t for t in titles)
    assert any("caching is active" in t for t in titles)


def test_caching_pct_never_exceeds_100():
    """cached tokens come from real usage; the component tree is a tiktoken
    estimate and can undershoot. The 'served from cache' percent must use the
    real prompt total and cap at 100, not print 117%."""
    tree = _tree(system=600, tools=(("a", 200),))  # component sum ~800
    res = analyze.analyze(tree, texts=[], input_per_1k=0.0025,
                          cached_tokens=65887, prompt_tokens=65959)
    body = next(f["body"] for f in res["findings"] if "served from cache" in f["body"])
    assert "100% of the prompt" in body  # 65887/65959 -> ~100, not /800 -> 8000%
    # falls back to the component sum when the real prompt total is absent, still capped
    res2 = analyze.analyze(tree, texts=[], input_per_1k=0.0025, cached_tokens=65887)
    body2 = next(f["body"] for f in res2["findings"] if "served from cache" in f["body"])
    assert "100% of the prompt" in body2


def test_analyze_clean_context():
    tree = {"name": "context", "tokens": 120,
            "children": [{"name": "system prompt", "tokens": 60, "children": []},
                         {"name": "user input", "tokens": 60, "children": []}]}
    res = analyze.analyze(tree, texts=["short user message"], input_per_1k=0.0025)
    assert [f["severity"] for f in res["findings"]] == ["ok"]
    assert res["reclaimable_usd"] == 0.0
    assert res["reclaimable_tokens"] == 0
