"""09 - The loop, with the real model: plan -> gate -> act -> verify.

Three runs against the live stack:
  A  auto tier      read a file, report it, finish.       No approval needed.
  B  confirm tier   write a file. The graph must STOP at the gate, hand back
                    what it would say out loud, and only act on "approve".
  C  cancel         same request, answer "cancel". Nothing may be written.

    .venv\\Scripts\\python.exe scripts\\09-loop-test.py
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langgraph.types import Command          # noqa: E402
from core.graph import build                 # noqa: E402

SB = ROOT / "sandbox"
fails = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -- ' + detail) if detail and not cond else ''}")
    fails += 0 if cond else 1


def run(graph, request: str, answer: str | None = None) -> tuple[dict, dict | None]:
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    t0 = time.time()
    state = graph.invoke({"request": request, "sources": {"local_files"}}, cfg)
    intr = None
    if "__interrupt__" in state:
        intr = state["__interrupt__"][0].value
        print(f"  [gate would say] {intr['speak']}")
        if answer:
            state = graph.invoke(Command(resume=answer), cfg)
    print(f"  [{time.time() - t0:.0f}s, {state.get('steps', 0)} plan steps]")
    return state, intr


graph = build()
probe = SB / "loop-probe.txt"
probe.write_text("the secret word is pelican\n")

print("\n== A: auto tier — read a file")
s, i = run(graph, f"Read the file {probe} and tell me what the secret word is.")
hist = s.get("history", [])
check("no approval was requested for a read", i is None)
check("read_file was actually called", any(h["tool"] == "read_file" for h in hist), str(hist)[:300])
check("that step passed verify", any(h["tool"] == "read_file" and h["verified"] for h in hist))
check("planner finished with done=true", s.get("proposed", {}).get("done") is True, str(s.get("proposed")))
check("planner saw the content (says pelican)", "pelican" in s.get("proposed", {}).get("spoken_summary", "").lower(),
      s.get("proposed", {}).get("spoken_summary"))

print("\n== B: confirm tier — write a file, approve")
out = SB / "loop-made.txt"
if out.exists():
    out.unlink()
s, i = run(graph, f"Create a new file at {out} containing exactly the text: hello from vajren", answer="approve")
check("graph paused at the gate", i is not None)
# ⚠ The safety property is that MUDIT SEES THE EXACT ARGUMENT before he
#   approves it — a planner talked into something describes `Remove-Item
#   -Recurse` as "tidying up". It used to be tested against `speak`, because
#   the gate read the literal aloud. It no longer does: a 325-character
#   PowerShell pipeline is five seconds of spoken punctuation that nobody can
#   check by ear, so the exact argument moved to `show`, printed verbatim in
#   the approval card, and the speech points at it. The guarantee is unchanged
#   and still asserted — it just has to be asserted where it now lives.
check("the approval card shows the exact path",
      i is not None and str(out) in i.get("show", ""), i and i.get("show"))
check("the spoken line at least names the file",
      i is not None and out.name in i["speak"], i and i["speak"])
check("gate reported the tool as write_file", i is not None and i.get("tool") == "write_file")
check("file exists after approval", out.exists())
check("content is what was asked", out.exists() and "hello from vajren" in out.read_text())
hist = s.get("history", [])
check("write step passed verify (sha matched)", any(h["tool"] == "write_file" and h["verified"] for h in hist), str(hist)[:300])
check("undo_ref recorded", any(h["observation"].get("undo_ref") for h in hist if h["tool"] == "write_file"))

print("\n== C: confirm tier — same request, CANCEL")
out2 = SB / "loop-cancelled.txt"
if out2.exists():
    out2.unlink()
s, i = run(graph, f"Create a new file at {out2} containing the text: this must never exist", answer="cancel")
check("graph paused at the gate", i is not None)
check("NOTHING was written on cancel", not out2.exists())
check("state shows not verified", s.get("verified") is False)

print("\n== D: barge-in — he interrupts a task that is going the wrong way")
# Mudit, 2026-09-08: "I see it going in the wrong direction and I have no
# option other than waiting for it to finish." ABORT stops the PLANNER at the
# next node boundary — never a step mid-flight, so nothing is half-applied —
# and whatever already ran stays done and is named out loud.
import threading                                                    # noqa: E402
import core.graph as G                                              # noqa: E402

out3 = SB / "barge-one.txt"
if out3.exists():
    out3.unlink()
G.ABORT.clear()
threading.Timer(12.0, G.ABORT.set).start()
s3, _ = run(graph, f"Create a file at {out3} containing one, then open notepad, "
                   f"then tell me the time", answer="approve")
G.ABORT.clear()
p3 = s3.get("proposed", {})
check("the graph stopped on the flag", bool(p3.get("_aborted")), str(p3)[:160])
check("it says it stopped", "stopped" in str(p3.get("spoken_summary", "")).lower(),
      str(p3.get("spoken_summary"))[:120])
check("it does not read like machine output",
      "write_file" not in str(p3.get("spoken_summary", "")), str(p3.get("spoken_summary"))[:120])
check("the trace records the interruption",
      any("aborted" in t for t in s3.get("trace", [])), str(s3.get("trace", []))[-160:])
# What already ran must NOT be rolled back: a stop is not an undo.
if any(h.get("tool") == "write_file" and h.get("verified") for h in s3.get("history", [])):
    check("work already finished is kept, not reverted", out3.exists())

# ⚠ The case this suite missed for nine days, and the reason it missed it: the
#   run above stops AFTER a write_file has verified, so `done_something` is true
#   and the done-guard never fires. On 2026-09-08 he stopped a task where a step
#   had been TRIED and FAILED and nothing had verified — the guard tripped, sent
#   the abort back to plan(), and on the third pass reported "I couldn't
#   actually do that, I kept describing it instead of doing it": the graph
#   blaming itself for a failure mode that never happened, about his own stop.
#   Driven through gate() directly rather than on a timer, because a race is not
#   a regression test.
print("\n== D2: a stop with nothing verified is still reported as a stop")
aborted_state = {
    "proposed": {"tool": "none", "args": {}, "done": True, "_aborted": True,
                 "spoken_summary": "Stopped — nothing had run yet."},
    # one step tried, none verified — the shape that tripped the guard
    "history": [{"tool": "focus_window", "args": {"title": "WhatsApp Beta"},
                 "verified": False, "observation": {"error": "no open window"}}],
    "trace": [], "steps": 1, "failures": 0, "request": "text someone", "sources": set(),
}
cmd = G.gate(aborted_state)
check("an aborted turn ends instead of being re-planned",
      getattr(cmd, "goto", None) in ("__end__", G.END), f"goto={getattr(cmd, 'goto', None)!r}")
check("the guard does not refuse it",
      not any("done refused" in t for t in (getattr(cmd, "update", {}) or {}).get("trace", [])),
      str((getattr(cmd, "update", {}) or {}).get("trace"))[:160])

# and cancelled() must hold the same line if an abort ever reaches it another way
c = G.cancelled({**aborted_state, "result": {"error": "declared done without doing anything"}})
say = str(c.get("proposed", {}).get("spoken_summary", "")).lower()
check("cancelled() reports a stop as a stop", "stopped" in say, say[:140])
check("cancelled() never blames itself for his stop",
      "describing it instead" not in say, say[:160])

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
