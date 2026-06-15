"""Headless dashboard QA harness.

Loads the real dashboard in a headless browser and asserts render invariants
instead of a human eyeballing screenshots. The invariants encode the bugs found
by hand: the "X found" pill must match the visible findings (the CSS class
collision that hid tip findings), nothing with text may be invisible, no
NaN/undefined leaks into the panel, and a reclaimable percent stays within
0..100.

Trust: the same checker is run against a deliberately broken DOM in
`test_checker_catches_a_broken_panel`. A checker that cannot fail is worthless,
so that test proves it has teeth (broken -> violations, healthy -> none).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

# A self-contained checker run in the page. Returns a list of violation strings
# for whatever view is currently rendered (checks skip elements that are absent).
CHECKS_JS = r"""
() => {
  const V = [];
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && cs.opacity !== '0'
      && cs.visibility !== 'hidden' && cs.display !== 'none' && el.offsetParent !== null;
  };

  // (1) optimization pill count must equal the number of VISIBLE non-ok findings,
  //     and no finding with text may be hidden. This is the class-collision guard.
  const pill = document.querySelector('#opt-count');
  if (pill) {
    const m = pill.textContent.match(/(\d+)\s*found/);
    const claimed = m ? parseInt(m[1], 10) : 0;
    const rows = [...document.querySelectorAll('#suggestions .sugg')];
    const visibleNonOk = rows.filter(r => !r.classList.contains('sev-ok') && visible(r)).length;
    if (claimed !== visibleNonOk)
      V.push(`optimization pill says ${claimed} but ${visibleNonOk} non-ok findings are visible`);
    for (const r of rows)
      if (r.textContent.trim() && !visible(r))
        V.push(`hidden finding: "${r.textContent.trim().slice(0, 50)}"`);
  }

  // (2) no broken values leaking into the rendered panel
  const main = document.querySelector('#main');
  if (main) {
    const t = main.innerText || '';
    for (const bad of ['NaN', 'undefined', '$?', 'Infinity'])
      if (t.includes(bad)) V.push(`main panel shows literal "${bad}"`);
  }

  // (3) a reclaimable percentage, when shown, must be within 0..100 (never exceed spend)
  const rb = document.querySelector('.reclaim-banner');
  if (rb) {
    const m = (rb.innerText || '').match(/([\d.]+)\s*%\s*of\s*spend/);
    if (m) {
      const p = parseFloat(m[1]);
      if (p < 0 || p > 100) V.push(`reclaimable ${p}% out of 0..100`);
    }
  }
  return V;
}
"""


def _checks(page):
    return page.evaluate(CHECKS_JS)


def test_calls_view_render_is_sound(page_and_errors, dashboard):
    """Open the codex trace (the shape that broke) and assert every finding the
    pill counts is actually visible, with no broken values or console errors."""
    page, errors = page_and_errors
    page.goto(dashboard, wait_until="load")
    page.wait_for_selector("button.call")
    page.click("button.call:has-text('gpt-5')")
    page.wait_for_selector("#suggestions .sugg")
    # the codex trace has three findings (1 warn + 2 tip); all must be visible
    # (inner_text is uppercased by CSS text-transform, so compare case-insensitively)
    assert page.locator("#opt-count").inner_text().strip().lower() == "3 found"
    assert _checks(page) == []
    assert errors == []


def test_trends_view_render_is_sound(page_and_errors, dashboard):
    page, errors = page_and_errors
    page.goto(dashboard, wait_until="load")
    page.wait_for_selector("button.call")
    page.click("#viewToggle button[data-view='trends']")
    page.wait_for_selector(".reclaim-banner")
    assert _checks(page) == []
    assert errors == []


def test_timeline_view_render_is_sound(page_and_errors, dashboard):
    page, errors = page_and_errors
    page.goto(dashboard, wait_until="load")
    page.wait_for_selector("button.call")
    page.click("#viewToggle button[data-view='timeline']")
    page.wait_for_selector("#main")
    # a two-turn run was seeded, so the timeline must not show the empty state
    assert "No multi-turn runs yet" not in page.locator("#main").inner_text()
    assert _checks(page) == []
    assert errors == []


# --- The checker must be able to fail. Prove it on a hand-built DOM. -----------

_BROKEN = """
<div id="main"><div class="panel">
  <div class="panel-title"><span>optimization</span>
    <span class="pill" id="opt-count">2 found</span></div>
  <div id="suggestions">
    <div class="sugg sev-warn"><span class="stext"><b>Warn</b> a visible finding</span></div>
    <div class="sugg sev-tip" style="position:fixed;opacity:0;max-width:280px">
      <span class="stext"><b>Tip</b> hidden by a class collision</span></div>
  </div>
</div></div>
"""

_HEALTHY = """
<div id="main"><div class="panel">
  <div class="panel-title"><span>optimization</span>
    <span class="pill" id="opt-count">2 found</span></div>
  <div id="suggestions">
    <div class="sugg sev-warn"><span class="stext"><b>Warn</b> a visible finding</span></div>
    <div class="sugg sev-tip"><span class="stext"><b>Tip</b> also visible</span></div>
  </div>
</div></div>
"""


def test_checker_catches_a_broken_panel(page_and_errors):
    """A bug-finding harness that cannot detect the bug is worse than none. This
    runs the exact production checker against the original failure (a tip finding
    hidden by opacity:0; position:fixed) and asserts it reports it - then that the
    healthy version is clean."""
    page, _ = page_and_errors

    page.set_content(_BROKEN)
    violations = page.evaluate(CHECKS_JS)
    assert any("says 2 but 1" in v for v in violations), violations  # count mismatch caught
    assert any(v.startswith("hidden finding") for v in violations), violations  # invisibility

    page.set_content(_HEALTHY)
    assert page.evaluate(CHECKS_JS) == []
