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
# ⚠ The first long link is NOT reliably a video. On 2026-09-07 YouTube served a
#   merch shelf at ref 21 — "From the official Lofi Girl store — records for the
#   long study nights." — which is over 20 chars, says nothing about a channel,
#   and opens a store page. The suite went red on a live-page layout, not on a
#   defect. The claim under test is "clicking a video result opens a watch
#   page", so try the candidates in order and let the first one that IS a video
#   answer it; only fail if none of them is.
def _candidates(listing: str) -> list:
    out = []
    for line in listing.splitlines():
        mm = re.match(r"(\d+): link '(.+)'", line)
        if mm and len(mm.group(2)) > 20 and "channel" not in mm.group(2).lower():
            out.append((int(mm.group(1)), mm.group(2)))
    return out


check("a video result was listed", bool(_candidates(f.get("listing", ""))), "\n".join(lines[:8]))
ref = label = None
c = {}
# ⚠ REFS ARE PER-SNAPSHOT. Re-finding after a failed click renumbers every
#   element, so carrying a ref from the first listing into the third attempt
#   clicks whatever now happens to hold that number. Re-derive the list from
#   the CURRENT snapshot each time and take the nth of that.
for attempt in range(12):
    cands = _candidates(browser_find("lofi").get("listing", ""))
    if attempt >= len(cands):
        break
    cand_ref, cand_label = cands[attempt]
    c = browser_click(cand_ref, cand_label)
    if "/watch" in c.get("url", ""):
        ref, label = cand_ref, cand_label
        break
    browser_open("https://www.youtube.com/results?search_query=lofi+hip+hop")
if ref:
    check("clicking it opens a watch page", "/watch" in c.get("url", ""), str(c)[:200])
    check("...and verify agrees", check_postcondition({"tool": "browser_click", "args": {"ref": ref, "label": label}}, c))
    w = browser_click(ref, "Buy now")
    check("a mismatched label is REFUSED", "error" in w and "labelled" in w["error"], str(w)[:200])
else:
    check("clicking it opens a watch page", False,
          "no candidate reached a watch page: "
          + "; ".join(l for _, l in _candidates(f.get("listing", ""))[:5])[:300])
# ⚠ Type on a KNOWN page, not on whatever the click above left us on. The
#   first version typed into the search box of the watch page the click opened;
#   Enter there autoplays a radio and the URL never contains the query, so the
#   assertion failed on a page state the test itself created. Go back to a
#   results page first.
browser_open("https://www.youtube.com/results?search_query=start")
s = browser_find("search")
m = re.search(r"(\d+): combobox 'Search'", s.get("listing", ""))
if m:
    t = browser_type(int(m.group(1)), "Search", "BGM", submit=True)
    check("typing a search and pressing enter searches", "search_query=BGM" in t.get("url", ""), str(t)[:200])
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
