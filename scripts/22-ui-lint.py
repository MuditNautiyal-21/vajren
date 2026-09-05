"""22 - Sanity-check the served page before a human ever opens it.

Not a real JS parser. It catches the things that actually broke this page:
a stray CSS token, unbalanced braces/parens in a script, an element the JS
queries by id that does not exist in the HTML, and a handler wired to nothing.
A blank black window with a console error is a terrible way to find out.
"""
from __future__ import annotations

import re
import sys
import urllib.request

URL = "http://127.0.0.1:7777/"
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

for fn in ("openMic", "startCapture", "stopCapture", "playWav", "connect", "frame"):
    check(f"{fn}() is defined", re.search(rf"function {fn}\b|{fn}\s*=\s*(async\s*)?\(", js) is not None)

check("mic is opened at load, not on keypress", "await openMic(" in js)
check("press() does not await before setting held",
      re.search(r"function press\([^)]*\)\{[^}]*held=true", js) is not None)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
