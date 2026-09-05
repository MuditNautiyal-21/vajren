"""ask.py - put any sentence in, watch Vajren work it out.

    .venv\\Scripts\\python.exe scripts\\ask.py "whatever you want"

Nothing here is task-specific. It hands your sentence to the planner and prints
each step it chooses. Confirmations are answered from the keyboard.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langgraph.types import Command          # noqa: E402
from core.graph import build                 # noqa: E402
from core.policy import POLICY               # noqa: E402

request = " ".join(sys.argv[1:]) or input("You: ")
auto_yes = "--yes" in sys.argv
graph, cfg = build(), {"configurable": {"thread_id": str(uuid.uuid4())}}

state = graph.invoke({"request": request, "sources": {"local_files"}}, cfg)
seen = 0
while True:
    for h in state.get("history", [])[seen:]:
        mark = "ok " if h["verified"] else "FAIL"
        print(f"  [{mark}] {h['tool']}({', '.join(f'{k}={str(v)[:60]!r}' for k, v in h['args'].items())})")
        if h["observation"].get("error"):
            print(f"         {h['observation']['error']}")
    seen = len(state.get("history", []))

    if "__interrupt__" not in state:
        break
    i = state["__interrupt__"][0].value
    print(f"\nVAJREN: {i['speak']}")
    heard = "yes go ahead" if auto_yes else input("You: ")
    answer = POLICY.interpret_confirmation(heard, 1.0)   # same parser voice will use
    print(f"  -> {answer}")
    state = graph.invoke(Command(resume="approve" if answer == "approve" else "cancel"), cfg)

print(f"\nVAJREN: {state.get('proposed', {}).get('spoken_summary', '(cancelled)')}")
