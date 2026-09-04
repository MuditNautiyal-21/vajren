"""
VAJREN entry point.

Right now this is a text REPL so you can exercise the plan/approve/act/verify
loop before the voice layer exists. Phase 03 swaps `input()` for the mic and
`print()` for Kokoro; nothing else in this file changes.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from core.graph import build  # noqa: E402
from core.policy import POLICY  # noqa: E402

BANNER = r"""
 __   __     _
 \ \ / /_ _ (_) _ _  ___  _ _
  \ V // _` || || '_|/ -_)| ' \
   \_/ \__,_|/ ||_|  \___||_||_|
           |__/   local. private. asks first.
"""


def speak(text: str) -> None:
    # Phase 03 replaces this with Kokoro streaming TTS.
    print(f"\n  Vajren: {text}\n")


def listen(prompt: str = "  you: ") -> tuple[str, float]:
    # Phase 03 replaces this with wake word + Silero VAD + Whisper.
    # Returns (text, confidence). Text input is confidence 1.0 by definition.
    return input(prompt).strip(), 1.0


def run(request: str, app) -> None:
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    stream = app.stream({"request": request, "sources": set()}, cfg)

    while True:
        interrupted = False
        for event in stream:
            if "__interrupt__" not in event:
                continue
            interrupted = True
            payload = event["__interrupt__"][0].value
            speak(payload["speak"])

            heard, conf = listen("  approve? ")
            verdict = POLICY.interpret_confirmation(heard, conf)

            if verdict == "unclear" and POLICY.confirmation.get("reask_once_on_low_confidence"):
                speak("Sorry, I didn't catch that clearly. Say 'yes go ahead' or 'cancel'.")
                heard, conf = listen("  approve? ")
                verdict = POLICY.interpret_confirmation(heard, conf)

            if verdict != "approve":
                speak("Cancelled. Nothing was done.")
            stream = app.stream(Command(resume=verdict), cfg)
            break
        if not interrupted:
            break


def main() -> None:
    print(BANNER)
    app = build()
    speak("Ready.")
    while True:
        try:
            request, _ = listen()
        except (EOFError, KeyboardInterrupt):
            speak("Shutting down.")
            return
        if request.lower() in {"quit", "exit", "stop vajren"}:
            speak("Shutting down.")
            return
        if request:
            run(request, app)


if __name__ == "__main__":
    main()
