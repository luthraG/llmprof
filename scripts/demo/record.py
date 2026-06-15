"""Record the demo animation against a real trace database.

Reproduces the original demo's recipe (1024x760 viewport, hard cuts, lossless
animated WebP plus a GIF) but drives the live dashboard so the scenes show real
data. Scenes: a terminal intro, the context flame graph, a flame drill-in tooltip,
the Trends view, and the Context timeline of the longest agent run.

The source database is COPIED to a temp dir and a throwaway proxy is started against
the copy on a free port, so the live ~/.llmprof db and any running proxy are never
touched. Frames are assembled with Pillow (animated WebP + GIF), so no ffmpeg/cwebp
is needed.

    python scripts/demo/record.py                       # uses ~/.llmprof/llmprof.db
    python scripts/demo/record.py --db /path/to.db --hold-ms 2600

Outputs assets/demo-v2.webp and assets/demo-v2.gif (alternates; the README is not
touched until you approve the swap).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
INTRO = ROOT / "scripts" / "demo" / "intro.html"
W, H = 1024, 760
SCALE = 2  # render at 2x then downscale for crisp text


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_healthy(base: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"proxy exited early ({proc.returncode})")
        try:
            with urllib.request.urlopen(base + "/llmprof/health", timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("proxy did not become healthy")


def _downscale(path: pathlib.Path) -> Image.Image:
    im = Image.open(path).convert("RGB")
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    return im


def record(db: str, hold_ms: int) -> list[pathlib.Path]:
    home = pathlib.Path(tempfile.mkdtemp(prefix="llmprof_demo_"))
    shutil.copy(db, home / "llmprof.db")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    runner = ("import uvicorn; from llmprof.proxy import create_app; "
              f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, log_level='warning')")
    proc = subprocess.Popen([sys.executable, "-c", runner],
                            env={**os.environ, "LLMPROF_HOME": str(home)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shots: list[pathlib.Path] = []
    try:
        _wait_healthy(base, proc)
        with sync_playwright() as p:
            br = p.chromium.launch()
            page = br.new_context(viewport={"width": W, "height": H},
                                  device_scale_factor=SCALE).new_page()

            def shot(name: str) -> None:
                path = home / f"{name}.png"
                page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": W, "height": H})
                shots.append(path)

            # 1. terminal intro
            page.goto(INTRO.as_uri(), wait_until="load")
            shot("1_intro")

            # 2. flame graph of a tool-rich call (the one whose context has the most
            #    tools under a single node), so the drill-down has something to show
            page.goto(base + "/", wait_until="load")
            page.wait_for_selector("button.call")
            pick = page.evaluate("""async () => {
                const list = await (await fetch('/llmprof/api/traces?limit=100')).json();
                let best = null;
                for (const t of (list.traces || []).slice(0, 50)) {
                    const d = await (await fetch('/llmprof/api/traces/' + t.id)).json();
                    for (const c of ((d.detail && d.detail.children) || [])) {
                        const n = (c.children || []).length;
                        if (n >= 2 && (!best || n > best.n)) best = {id: t.id, name: c.name, n};
                    }
                }
                if (best) window.select(best.id);
                return best;
            }""")
            page.wait_for_selector("#flame svg")
            page.wait_for_timeout(500)
            shot("2_flame")

            # 3. click that node to zoom one level down and reveal the individual tools.
            #    Match the full normalized label (the truncated label is a prefix of the
            #    name) so "tool schemas" is not confused with "tool results"/"tool calls".
            if pick:
                target = page.evaluate("""(name) => {
                    const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                    const want = norm(name);
                    for (const g of document.querySelectorAll('#flame g.frame')) {
                        const rect = g.querySelector('rect'), txt = g.querySelector('text');
                        if (!rect || +rect.getAttribute('y') === 0) continue;  // skip root row
                        const label = norm(txt && txt.textContent);
                        const ok = want.startsWith(label) || label.startsWith(want);
                        if (label.length >= 4 && ok) {
                            const b = rect.getBoundingClientRect();
                            return {x: b.x + b.width / 2, y: b.y + b.height / 2};
                        }
                    }
                    return null;
                }""", pick["name"])
                if target:
                    page.mouse.click(target["x"], target["y"])
                    page.wait_for_timeout(300)
                    page.mouse.move(5, 5)          # clear the tooltip for a clean zoomed shot
                    page.wait_for_timeout(250)
            shot("3_drill")
            n_children = pick["n"] if pick else 0
            print(f"drilled into {pick['name']!r} ({n_children} children)" if pick else "no tools")

            # 4. trends
            page.click("#viewToggle button[data-view='trends']")
            page.wait_for_selector(".reclaim-banner")
            page.wait_for_timeout(500)
            shot("4_trends")

            # 5. timeline of the longest run (most turns)
            page.click("#viewToggle button[data-view='timeline']")
            page.wait_for_timeout(300)
            picked = page.evaluate("""async () => {
                const s = await (await fetch('/llmprof/api/sessions')).json();
                const runs = s.sessions || s || [];
                if (!runs.length) return null;
                const best = runs.slice().sort((a,b)=>(b.turns||0)-(a.turns||0))[0];
                window.selectedSession = best.session_id;
                window.renderTimeline(true);
                return best.turns;
            }""")
            page.wait_for_timeout(500)
            shot("5_timeline")
            print(f"timeline run has {picked} turns")
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return shots


def assemble(shots: list[pathlib.Path], hold_ms: int) -> None:
    frames = [_downscale(s) for s in shots]
    out_webp = ROOT / "assets" / "demo-v2.webp"
    out_gif = ROOT / "assets" / "demo-v2.gif"
    frames[0].save(out_webp, format="WEBP", save_all=True, append_images=frames[1:],
                   duration=hold_ms, loop=0, lossless=True, quality=100, method=6)
    frames[0].save(out_gif, format="GIF", save_all=True, append_images=frames[1:],
                   duration=hold_ms, loop=0, optimize=True)
    print(f"wrote {out_webp.relative_to(ROOT)} and {out_gif.relative_to(ROOT)} "
          f"({len(frames)} frames, {hold_ms}ms each)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(pathlib.Path.home() / ".llmprof" / "llmprof.db"))
    ap.add_argument("--hold-ms", type=int, default=2600)
    args = ap.parse_args()
    if not pathlib.Path(args.db).exists():
        sys.exit(f"no trace db at {args.db}")
    shots = record(args.db, args.hold_ms)
    assemble(shots, args.hold_ms)


if __name__ == "__main__":
    main()
