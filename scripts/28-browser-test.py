"""28 - Can it actually work a web page, and does the gate still hold there?

Vajren's own Chrome (core/browser.py), driven three ways:

  DIRECT     the tools themselves: open, find, click a real result, type a
             search, refuse a wrong label, refuse a password field.
  POLICY     which labels take a click out of the once-per-request grant.
  PLANNED    the request Mudit actually gave — search YouTube and open the
             first result — through the real graph, approving once, and
             asserting it clicked a result rather than describing one.

    .venv\\Scripts\\python.exe -X utf8 scripts\\28-browser-test.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langgraph.types import Command                                   # noqa: E402

from core import browser                                              # noqa: E402
from core.graph import build                                          # noqa: E402
from core.policy import POLICY                                        # noqa: E402
from core.tools.web import (browser_click, browser_find, browser_open,  # noqa: E402
                            browser_read, browser_type)
from core.verify import check_postcondition                           # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


print("\n== direct: open, find, click a result, type a search")
r = browser_open("https://www.youtube.com/results?search_query=lofi+hip+hop")
check("opened the search page", "error" not in r and "youtube.com" in r.get("url", ""), str(r)[:200])
check("...and verify agrees", check_postcondition(
    {"tool": "browser_open", "args": {"url": "https://www.youtube.com/results?search_query=lofi+hip+hop"}}, r))
f = browser_find("lofi")
lines = f.get("listing", "").splitlines()
check("find returns numbered elements", f.get("count", 0) >= 3 and lines and re.match(r"\d+: ", lines[0]), f.get("listing", "")[:200])
ref = label = None
for ln in lines:
    m = re.match(r"(\d+): link '(.+)'", ln)
    if m and len(m.group(2)) > 20 and "channel" not in m.group(2).lower():
        ref, label = int(m.group(1)), m.group(2)
        break
check("a video result was listed", ref is not None, "\n".join(lines[:8]))
if ref:
    c = browser_click(ref, label)
    check("clicking it opens a watch page", "/watch" in c.get("url", ""), str(c)[:200])
    check("...and verify agrees", check_postcondition({"tool": "browser_click", "args": {"ref": ref, "label": label}}, c))
    w = browser_click(ref, "Buy now")
    check("a mismatched label is REFUSED", "error" in w and "labelled" in w["error"], str(w)[:200])
s = browser_find("search")
m = re.search(r"(\d+): combobox 'Search'", s.get("listing", ""))
if m:
    t = browser_type(int(m.group(1)), "Search", "BGM", submit=True)
    check("typing a search and pressing enter navigates", t.get("navigated") and "BGM" in t.get("url", ""), str(t)[:200])
    check("...and verify agrees", check_postcondition({"tool": "browser_type", "args": {"ref": int(m.group(1)), "label": "Search", "text": "BGM"}}, t))
rd = browser_read()
check("read returns untrusted page text", rd.get("untrusted") and len(rd.get("content", "")) > 100)

print("\n== direct: a password field is refused outright")
browser_open("https://github.com/login")
pf = browser_find("password")
m = re.search(r"(\d+): password", pf.get("listing", "")) or re.search(r"(\d+): .*PASSWORD", pf.get("listing", ""))
check("password field is flagged in the listing", "PASSWORD" in pf.get("listing", ""), pf.get("listing", "")[:300])
if m:
    pw = browser_type(int(m.group(1)), "Password", "hunter2")
    check("typing into it is refused", "error" in pw and "password" in pw["error"].lower(), str(pw)[:200])

print("\n== policy: which clicks may never ride on an earlier yes")
for lab in ("Subscribe", "Place order", "Post comment", "Delete", "Send", "Buy now"):
    check(f"{lab!r} asks every time", bool(POLICY.needs_fresh_confirmation("browser_click", {"ref": 1, "label": lab})))
for lab in ("lofi hip hop radio", "Postal codes", "Home", "Next page", "Search"):
    check(f"{lab!r} rides on the request's yes", not POLICY.needs_fresh_confirmation("browser_click", {"ref": 1, "label": lab}))
for t in ("browser_open", "browser_click", "browser_type"):
    check(f"{t} is once-per-request", t in POLICY.confirm_once)
for t in ("browser_read", "browser_find", "browser_back"):
    check(f"{t} needs no approval", t not in POLICY.confirm_once and t in set(POLICY._auto))

print("\n== planned: 'search YouTube for lofi and open the first video' — one yes")
app = build()
cfg = {"configurable": {"thread_id": f"browser-test-{int(time.time())}"}}
state = app.invoke({"request": "In your own browser, search YouTube for lofi hip hop and open the "
                               "first video result. Then tell me its title.",
                    "sources": set()}, cfg)
gates = 0
first_speak = ""
while "__interrupt__" in state and gates < 8:
    gates += 1
    if gates == 1:
        first_speak = state["__interrupt__"][0].value.get("speak", "")
    state = app.invoke(Command(resume="approve"), cfg)
tools = [h["tool"] for h in state.get("history", [])]
print(f"    approvals: {gates}   tools: {tools}")
print(f"    first ask: {first_speak!r}")
print(f"    said: {state.get('proposed', {}).get('spoken_summary', '')!r}")
check("it used the browser", "browser_open" in tools)
check("it found and clicked something", "browser_find" in tools and "browser_click" in tools, str(tools))
check("one approval covered the whole thing", gates == 1, f"asked {gates} times")
check("it ended on a watch page", "/watch" in browser.read().get("url", ""), browser.read().get("url"))

browser.close()
print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
