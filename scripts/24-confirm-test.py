"""24 - Does the approval gate understand a person?

Two halves, and both matter:

  PERMISSIVE  natural agreement and refusal must work. "yeah do it", "sure",
              "nope", "not now" — a gate that only accepts a magic phrase is
              deaf, and deaf is not the same as safe.

  STRICT      nothing ambiguous, hesitant, negated or interrogative may ever
              come back as approve. This half is the one that must never
              regress, so it is deliberately adversarial.

Cases marked * exercise the reflex-model fallback (they miss the phrase lists).

    .venv\\Scripts\\python.exe -X utf8 scripts\\24-confirm-test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import confirm                     # noqa: E402

PENDING = ("I'll create the file notes.txt with the text hello. "
           "The file is C:\\vajren\\sandbox\\notes.txt. Should I go ahead?")

APPROVE = ["yes go ahead", "Yes, go ahead.", "yeah", "yep", "sure", "ok", "okay",
           "do it", "go for it", "please do", "confirmed", "sounds good",
           "yeah do it", "sure, go ahead", "alright do it", "yes please"]

CANCEL = ["cancel", "Cancel.", "no", "nope", "nah", "stop", "don't", "do not do that",
          "never mind", "forget it", "wait", "hold on", "not now", "leave it",
          "no cancel that", "don't go ahead", "no, go ahead with the other one",
          "actually no", "hmm, not yet", "maybe later", "I guess so",
          "if you think it's right", "let me think about it"]

NOT_APPROVE = ["what is in that file?", "why do you want to do that?",
               "which file did you say?", "where is it going?",
               "hmm", "uh", "what?", "", "the weather is nice",
               "read me the file first"]

fails = 0
slow = []


def check(name, ok, detail=""):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


def ask(text):
    t0 = time.perf_counter()
    d, r = confirm.resolve(text, 1.0, PENDING)
    dt = time.perf_counter() - t0
    if dt > 0.5:
        slow.append((text, round(dt, 1)))
    return d, r, dt


print("\n== natural agreement must APPROVE")
for p in APPROVE:
    d, r, dt = ask(p)
    check(f"{p!r} -> approve", d == "approve", f"got {d!r}")

print("\n== refusal, hesitation and deferral must CANCEL")
for p in CANCEL:
    d, r, dt = ask(p)
    check(f"{p!r} -> cancel", d == "cancel", f"got {d!r}")

print("\n== questions and noise must NEVER approve, and should get a real answer")
for p in NOT_APPROVE:
    d, r, dt = ask(p)
    check(f"{p!r} does not approve", d != "approve", f"got {d!r}")
    if d == "neither":
        ok = bool(r.strip()) and "yes go ahead" not in r.lower()[:0] and len(r) > 10
        check(f"    ...and replies with something useful", ok, repr(r))

print("\n== the model may never overturn a negation")
for p in ["don't go ahead", "no, do it", "not that one, go ahead"]:
    d, _, _ = ask(p)
    check(f"{p!r} -> cancel", d == "cancel", f"got {d!r}")

if slow:
    print(f"\n  {len(slow)} answers needed the model (>0.5s):")
    for t, dt in slow[:8]:
        print(f"    {dt:5.1f}s  {t!r}")

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
