"""
VAJREN entry point — the conversation loop.

Text for now. Phase 03 swaps `listen()` for wake word + VAD + Whisper and
`speak()` for Kokoro; nothing else in this file changes. That is the whole
reason the two are functions rather than bare input()/print().

    .venv\\Scripts\\python.exe -m core.main

What this adds over scripts/ask.py, which is one-shot:
  - one conversation, so "now do the same for the other folder" resolves
  - an episode per request, OPENED and CLOSED, so the audit trail has endings
  - /undo, which finally makes the undo_ref machinery reachable by a human
  - every step shown as it happens, verified or not
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from langgraph.types import Command                                    # noqa: E402
from core.graph import build                                           # noqa: E402
from core.policy import POLICY                                         # noqa: E402
from core.tools import close_episode, last_undoable, run_tool          # noqa: E402
from core.verify import check_postcondition                            # noqa: E402

BANNER = r"""
 __   __     _
 \ \ / /_ _ (_) _ _  ___  _ _
  \ V // _` || || '_|/ -_)| ' \
   \_/ \__,_|/ ||_|  \___||_||_|
           |__/   local. private. asks first.
"""

HELP = """  /undo    reverse the last file change Vajren made
  /steps   what it did on the last request
  /help    this
  quit     leave (Ctrl+C works too)"""


def speak(text: str) -> None:
    # Phase 03 replaces this with Kokoro streaming TTS.
    print(f"\n  Vajren: {text}\n")


def listen(prompt: str = "  you: ") -> tuple[str, float]:
    # Phase 03 replaces this with wake word + Silero VAD + Whisper.
    # Returns (text, confidence). Typed input is confidence 1.0 by definition.
    return input(prompt).strip(), 1.0


def show(step: dict) -> None:
    args = ", ".join(f"{k}={str(v)[:50]!r}" for k, v in step["args"].items())
    print(f"      {'ok ' if step['verified'] else 'FAILED'}  {step['tool']}({args})")
    err = step["observation"].get("error")
    if err:
        print(f"             {err}")


def ask(app, request: str, conversation: list[dict]) -> dict:
    """One request, start to finish. Returns the final state."""
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    t0 = time.perf_counter()
    state = app.invoke({"request": request, "sources": {"local_files"},
                        "conversation": conversation}, cfg)
    shown = 0

    while True:
        for step in state.get("history", [])[shown:]:
            show(step)
        shown = len(state.get("history", []))

        if "__interrupt__" not in state:
            break

        payload = state["__interrupt__"][0].value
        speak(payload["speak"])
        heard, conf = listen("  approve? ")
        verdict = POLICY.interpret_confirmation(heard, conf)

        if verdict == "unclear" and POLICY.confirmation.get("reask_once_on_low_confidence"):
            speak("Sorry — say 'yes go ahead' or 'cancel'.")
            heard, conf = listen("  approve? ")
            verdict = POLICY.interpret_confirmation(heard, conf)

        # Anything that is not a clear approval is a cancel. Silence, ambiguity
        # and "hmm" all mean no; only "yes go ahead" means yes.
        state = app.invoke(Command(resume="approve" if verdict == "approve" else "cancel"), cfg)

    # Close the episode the graph opened, so episodes.outcome stops being NULL.
    ep = state.get("episode_id")
    if ep:
        done = bool(state.get("proposed", {}).get("done"))
        err = (state.get("result") or {}).get("error")
        close_episode(ep, "completed" if done else ("cancelled" if not err else "failed"), err)

    state["_elapsed"] = time.perf_counter() - t0
    return state


def do_undo() -> None:
    row = last_undoable()
    if not row:
        speak("Nothing to undo — I haven't changed any files yet.")
        return
    tool, undo_ref, path = row
    speak(f"That would put back {path} (undoing the last {tool}). Say 'yes go ahead' or 'cancel'.")
    heard, conf = listen("  approve? ")
    if POLICY.interpret_confirmation(heard, conf) != "approve":
        speak("Left it as it is.")
        return
    # /undo bypasses the graph, so it must run the post-condition itself. Without
    # this it would report "Restored X" on the tool's say-so — the exact class of
    # claim the verify node exists to stop.
    action = {"tool": "undo_file", "args": {"undo_ref": undo_ref}}
    r = run_tool(action)
    if r.get("error"):
        speak(f"Couldn't undo that: {r['error']}")
        return
    if check_postcondition(action, r):
        speak(f"Restored {r['restored']}. Checked — it's back.")
    else:
        speak(f"I ran the undo but could NOT confirm {path} is back. Check it yourself.")


def main() -> None:
    print(BANNER)
    app = build()
    conversation: list[dict] = []
    last: dict = {}
    speak("Ready. Type /help if you want the commands.")

    while True:
        try:
            request, _ = listen()
        except (EOFError, KeyboardInterrupt):
            speak("Shutting down.")
            return

        if not request:
            continue
        low = request.lower()
        if low in {"quit", "exit", "stop vajren", "/quit"}:
            speak("Shutting down.")
            return
        if low == "/help":
            print(HELP)
            continue
        if low == "/undo":
            do_undo()
            continue
        if low == "/steps":
            steps = last.get("history", [])
            if not steps:
                print("      (nothing yet)")
            for s in steps:
                show(s)
            continue

        try:
            last = ask(app, request, conversation)
        except KeyboardInterrupt:
            # Ctrl+C abandons the request, not the session. Nothing half-done is
            # left running: the tool either completed before the interrupt or
            # never started, and either way the audit row reflects reality.
            speak("Stopped that one. Still here.")
            continue

        answer = last.get("proposed", {}).get("spoken_summary") or "Cancelled."
        speak(f"{answer}   [{last['_elapsed']:.0f}s]")
        conversation.append({"request": request, "outcome": answer})


if __name__ == "__main__":
    main()
