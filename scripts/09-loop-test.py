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
check("the spoken line names the exact file", i is not None and str(out) in i["speak"], i and i["speak"])
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

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
