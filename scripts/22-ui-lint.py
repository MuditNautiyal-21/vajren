"""22 - Sanity-check the served page before a human ever opens it.

Not a real JS parser. It catches the things that actually broke this page:
a stray CSS token, unbalanced braces/parens in a script, an element the JS
queries by id that does not exist in the HTML, and a handler wired to nothing.
A blank black window with a console error is a terrible way to find out.
"""
from __future__ import annotations

import re
import os
import sys
import urllib.request

PORT = os.environ.get("VAJREN_FACE_PORT", "7777")
URL = f"http://127.0.0.1:{PORT}/"
fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


html = urllib.request.urlopen(URL, timeout=20).read().decode("utf-8", "replace")
print(f"\n  {len(html):,} bytes from {URL}")

check("has a canvas", 'id="c"' in html)
check("script tags balanced", html.count("<script>") == html.count("</script>"),
      f"{html.count('<script>')} open, {html.count('</script>')} close")

scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
check("at least one script block", len(scripts) >= 1, f"found {len(scripts)}")

# ⚠ Parse with node, not with regexes. A hand-rolled brace counter reported an
# imbalance in a file node accepts happily — template literals with ${} nested
# inside them are exactly what a counter gets wrong, and a false alarm in a lint
# is worse than no lint, because the next real one gets ignored.
import subprocess
import tempfile
have_node = subprocess.run(["node", "--version"], capture_output=True, text=True).returncode == 0
if have_node:
    for i, s in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(s)
            tmp = f.name
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        first = (r.stderr.strip().splitlines() or [""])[:6]
        check(f"script {i+1} parses as valid JavaScript", r.returncode == 0, " | ".join(first))
else:
    check("node available to parse the JS", False, "install node, or lint by eye")

js = "\n".join(scripts)

# every $('#id') the JS touches must exist in the markup
ids_used = set(re.findall(r"\$\('#([A-Za-z0-9_]+)'\)", js)) | set(re.findall(r"pill\('#([A-Za-z0-9_]+)'", js))
ids_have = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))
missing = sorted(ids_used - ids_have)
check("every element the JS queries exists", not missing, f"missing: {missing}")

# the CSS must not contain a stray identifier where a value belongs
css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
bad = [ln.strip() for ln in css.splitlines() if re.search(r":\s*#[0-9a-fA-F]*[g-zG-Z]", ln)]
check("no malformed hex colours in CSS", not bad, str(bad[:2]))

for fn in ("openMic", "startCapture", "stopCapture", "playWav", "connect", "frame",
           "loadBrain", "brainPos", "pinned", "startClock", "pauseClock", "resetClock", "shownMs"):
    check(f"{fn}() is defined", re.search(rf"function {fn}\b|{fn}\s*=\s*(async\s*)?\(", js) is not None)

check("mic is opened at load, not on keypress", "await openMic(" in js)
check("press() does not await before setting held",
      re.search(r"function press\([^)]*\)\{[^}]*held=true", js) is not None)

# ---- v2.1 ----------------------------------------------------------------
# The face must not assume 60 Hz. Every one of these was a per-frame increment
# once, which ran 2.4x fast on a 144 Hz panel; if any of them loses its `kt`
# the face silently goes back to being monitor-dependent, and nothing else
# in the suite would notice.
check("the face advances on real time, not frame count", "T+=dt" in js)
for name, pat in (("spin", r"spin\+=[^;]*\bkt\b"), ("rot", r"rot\+=[^;]*\bkt\b"),
                  ("ripple", r"ripple\+=[^;]*\bkt\b"), ("dust", r"d\.a\+=[^;]*\bkt\b"),
                  ("motes", r"m\.a\+=[^;]*\bkt\b"), ("thoughts", r"q\.t\+=[^;]*\bkt\b")):
    check(f"{name} is scaled by the frame delta", re.search(pat, js) is not None)
# `k` is the loop counter inside frame(); a frame clock named the same would be
# shadowed by it and stop advancing, invisibly.
check("the frame clock is not named k", not re.search(r"const k=dt", js))

# Idle must not burn the GPU the model is using.
check("idle frames are throttled", "document.hidden" in js and "busy?0:1000/" in js)

# ⚠ 2026-09-08, the hour after this shipped. Windows' "Show animations in
# Windows" is a PERFORMANCE toggle, and Chrome reports it as
# prefers-reduced-motion. The first version let that freeze the orb solid, and a
# frozen orb reads as "the assistant is off" — the one thing this screen must
# never say by accident. The OS hint governs page chrome; the orb is his call and
# defaults to alive. Both halves are guarded here because the tempting "fix" is
# to wire the media query straight back into the canvas.
check("the orb defaults to alive, whatever the OS says", "let motion='full', drift=1" in js)
check("drift is never zeroed", not re.search(r"drift\s*=\s*(?:REDUCED\?0|0\b)", js))
check("motion has an explicit, remembered override", "setMotion(" in js and "vajren.motion" in js)
check("the OS hint is read, and only to inform", "OS_CALM" in js)
check("page chrome still honours reduced motion", "prefers-reduced-motion" in css)

# ⚠ Two clocks. T is real seconds and never stops, because a lit memory fades on
# it; TD is decoration and runs at whatever pace `drift` says. Crossing them is
# the whole bug class this pair exists to prevent — a decoration reading T
# ignores calm motion entirely, and a decay reading TD slows down with it and
# quietly starts lying about how long a memory has been lit.
check("the decoration clock exists", "TD+=dt*drift" in js)
check("memory decay reads the truth clock", re.search(r"now\s*=\s*T\b", js) is not None)
check("breathe reads the decoration clock", re.search(r"breathe=1\+Math\.sin\(TD", js) is not None)
check("the tick ring reads the decoration clock", re.search(r"6\.2832\+TD\*\.05", js) is not None)
check("the HUD arcs read the decoration clock", re.search(r"\.2\*Math\.sin\(TD\*1\.7", js) is not None)
# The invariant, stated once and checked absolutely: NOTHING decorative reads T.
# T survives only as `T+=dt` and as the clock a lit memory decays on.
strays = re.findall(r".{0,40}\bT\*.{0,20}", js)
check("no decoration reads the truth clock", not strays, f"{len(strays)}: {strays[:2]}")

# ⚠ Every frame budget must stay UNDER the dt clamp. Miss this and nothing looks
# broken — time just runs slow, uniformly, and a lit memory outstays its welcome.
clamp = re.search(r"Math\.min\((\.\d+),\(ts-prev\)/1000\)", js)
budgets = [1000 / int(d) for d in re.findall(r"busy\?0:1000/(\d+)\)", js)]
check("every frame budget is under the dt clamp",
      bool(clamp) and bool(budgets) and max(budgets) / 1000 <= float(clamp.group(1)),
      f"clamp={clamp.group(1) if clamp else '?'}s, budgets={[round(b) for b in budgets]}ms")

# ⚠ The clock reports MACHINE time for the whole turn. It must pause (not reset)
# when the turn hands back to him, or a gated multi-step task reports its last
# leg and calls that the answer — the exact number he is trying to judge.
check("the clock accumulates across gates", "workAcc+=performance.now()-workStart" in js)
check("leaving a working state pauses, never resets", "if(working) startClock(); else pauseClock();" in js)
check("a new turn resets the clock", js.count("resetClock()") >= 3,
      "reset belongs on typed send, on a new utterance, and nowhere in setState")

# The two ways out of a running task, and the fact that the screen admits to them.
check("ESC stops a running task", '{type:\'stop\'}' in js.replace('"', "'"))
check("the screen says how to interrupt", "HINT_WORK" in js and "ESC" in js)
check("there is a visible stop control", 'id="stop"' in html)

# The gate is answerable on the card, not only 400px away in the dock.
check("the approval card carries its own yes/cancel",
      re.search(r"act\.appendChild\(y\)", js) is not None)
# ⚠ and it must still be a message, never the action: nothing on that card may
#   carry a tool name or argument of its own.
ask_block = re.search(r"if\(cls==='ask'\)\{const hn.*?d\.appendChild\(act\);\}", js, re.S)
check("the card's buttons only send approve/cancel",
      ask_block is not None
      and set(re.findall(r"type:'([a-z_]+)'", ask_block.group(0))) == {"approve", "cancel"},
      "a gate button must carry no action of its own")

# A log that yanks itself to the bottom is a log he cannot read.
check("the log only follows when already at the bottom", "wasPinned" in js)
check("but an approval always shows itself", "wasPinned||cls==='ask'" in js)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
