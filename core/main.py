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
import traceback
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


# Voice is OPT-IN (`--voice`) and degrades on its own. Text always works: if the
# headset is unplugged mid-session, or a model file is missing, or stdin is a
# pipe, you get typing rather than a traceback.
VOICE = {"on": False}


def speak(text: str) -> None:
    print(f"\n  Vajren: {text}\n")
    if VOICE["on"]:
        from core import voice
        voice.say(text)          # returns False if it could not; text already shown


def listen(prompt: str = "  you: ") -> tuple[str, float]:
    """(text, confidence). Typed input is 1.0 by definition; speech is scored."""
    if VOICE["on"]:
        from core import voice
        print(f"{prompt}[listening]", end="", flush=True)
        heard, conf = voice.listen_once()
        if heard:
            print(f"\r{prompt}{heard}   ({conf:.2f})")
            return heard, conf
        # Heard nothing. Fall through to typing rather than looping on a mic
        # that may be muted — a voice assistant that cannot be talked to and
        # will not let you type is just broken.
        print(f"\r{prompt}[nothing heard — type instead] ", end="", flush=True)
    return input(prompt if not VOICE["on"] else "").strip(), 1.0


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

    if "--voice" in sys.argv:
        from core import voice
        av = voice.available()
        if av["tts"] and av["stt"] and av["audio"]:
            VOICE["on"] = True
            d = av.get("devices", {})
            print(f"  voice on   in: {d.get('input_name')}   out: {d.get('output_name')}")
            # ⚠ Say this out loud rather than only printing it. If the headset is
            #   on the desk instead of his head, the very first thing Vajren does
            #   should reveal that — not the first approval prompt.
        else:
            missing = [k for k in ("tts", "stt", "audio") if not av[k]]
            print(f"  voice OFF - {', '.join(missing)} unavailable: {av['why']}")
            print("  run: .venv\\Scripts\\python.exe scripts\\16-get-voice-models.py")

    app = build()
    conversation: list[dict] = []
    last: dict = {}
    speak("Ready. Type slash help if you want the commands."
          if VOICE["on"] else "Ready. Type /help if you want the commands.")

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
        except Exception as e:                                # noqa: BLE001
            # ⚠ Never show a traceback to someone who is talking to an assistant.
            #   A dead gateway produced ~100 lines of httpx/instructor/langgraph
            #   frames whose one useful word was "refused". Say what is wrong and
            #   what fixes it; the detail goes to the log, not the conversation.
            blob = f"{type(e).__name__}: {e}"
            if "Connection" in blob or "refused" in blob or "APIConnection" in blob:
                speak("I can't reach my own brain — the model stack isn't running. "
                      "Start it with scripts\\04-start-stack.ps1 and try again.")
            else:
                speak(f"That went wrong: {blob[:200]}")
            (ROOT / "logs").mkdir(exist_ok=True)
            with open(ROOT / "logs" / "repl-errors.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- {request!r}\n{traceback.format_exc()}")
            continue
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
