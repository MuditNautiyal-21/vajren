"""25 - Does it DO a multi-part request, or just describe one?

THE BUG THIS SUITE EXISTS FOR (J-040). Asked to "open Chrome, pick the PCYT
profile, go to LinkedIn and search for me", Vajren replied in 5.5 seconds with
"I'll open Chrome with the PCYT profile and navigate to LinkedIn" and marked the
request COMPLETE. Zero tools ran. Every request containing more than one action
failed the same way, and it read as the assistant simply not working.

The cause was a guard I had loosened myself: `done` was only challenged when a
step had already been attempted, so a `done` on the very first plan step went
straight through. The distinction that actually matters is TENSE — an answer
reports, a plan promises — so that is what this suite pins down.

  .venv\\Scripts\\python.exe -X utf8 scripts\\25-multistep-test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.graph import _PROMISE, build          # noqa: E402
from langgraph.types import Command             # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


# -- the tense test, in isolation and fast --------------------------------
print("\n== a promise must be recognised as a promise")
PROMISES = [
    "I'll open Chrome with the PCYT profile and navigate to LinkedIn.",
    "I will search for the file and open it for you.",
    "Let me search for the file about UB's master's program.",
    "I'm going to write the essay and then open it.",
    "Now I'll bring the window to the front.",
]
RESULTS = [
    "I'm good, thanks — what do you need?",
    "Chrome is open on your LinkedIn profile.",
    "The essay is saved in your sandbox and open in Notepad.",
    "Both Notepad windows are closed.",
    "The file contains a short essay on UB's data science program.",
    "Vajren. I run entirely on this machine.",
]
for t in PROMISES:
    check(f"promise: {t[:44]!r}", bool(_PROMISE.search(t)))
for t in RESULTS:
    check(f"result : {t[:44]!r}", not _PROMISE.search(t))

# -- end to end, through the real graph -----------------------------------
# Approve everything. This suite is about whether the work HAPPENS, not about
# whether the gate holds — 24-confirm-test.py owns that question.
print("\n== a two-part request must run two tools, not narrate one")
target = ROOT / "sandbox" / "multistep-test.txt"
if target.exists():
    target.unlink()

app = build()
# ⚠ A FRESH thread id every run. The checkpointer is durable by design, so
#   reusing one resumes the COMPLETED state of the last run: the graph returns
#   instantly, nothing executes, and the test fails on a file it never asked
#   anyone to write. Passed standalone, failed in the suite, for exactly this.
cfg = {"configurable": {"thread_id": f"multistep-test-{int(time.time())}"}}
state = app.invoke({"request":
                    f"Write the single word ready into {target}, then read that "
                    f"file back to me and tell me what it says.",
                    "sources": set()}, cfg)

steps = 0
while "__interrupt__" in state and steps < 12:
    steps += 1
    state = app.invoke(Command(resume="approve"), cfg)

hist = state.get("history", [])
tools = [h["tool"] for h in hist]
print(f"    tools run: {tools}")
check("it actually ran tools", bool(tools), "ran nothing at all")
check("the file was written", target.exists())
check("it wrote AND read (two distinct steps)",
      "write_file" in tools and "read_file" in tools, f"got {tools}")
check("every step it took was verified", all(h["verified"] for h in hist),
      str([(h["tool"], h["verified"]) for h in hist]))
summary = state.get("proposed", {}).get("spoken_summary", "")
print(f"    said: {summary!r}")
check("it finished with a result, not a promise", not _PROMISE.search(summary), summary)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
