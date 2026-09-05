"""17 - Prove the voice stack without a human in the room.

Nobody can speak into the mic on demand, and a voice feature that is only ever
tested by its author saying "yes go ahead" once is not tested at all.

So: synthesize each phrase with Kokoro, write a WAV, feed that WAV to Whisper,
and check the text comes back. That exercises both halves on real audio, and it
runs in CI, at 3am, forever.

The phrases that matter most are the CONFIRMATION phrases. Every mutating action
in Vajren is gated on hearing one. If Whisper renders "yes go ahead" as
"Yes, go ahead." and the parser only matches the bare lowercase form, then every
approval silently becomes a cancel and the assistant looks broken but safe. The
opposite mistake - a cancel heard as approval - is the one that loses files.
Both are tested here.

    .venv\\Scripts\\python.exe scripts\\17-voice-roundtrip.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import voice                          # noqa: E402
from core.policy import POLICY                  # noqa: E402

OUT = ROOT / "sandbox" / "voice-test"
OUT.mkdir(parents=True, exist_ok=True)
fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() and " ".join(
        re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()) or ""


print("\n== availability")
av = voice.available()
check("TTS model loads", av["tts"], str(av["why"].get("tts")))
check("STT model loads", av["stt"], str(av["why"].get("stt")))
check("audio devices found", av["audio"], str(av["why"].get("audio")))
if av.get("devices"):
    print(f"        in : {av['devices'].get('input_name')}")
    print(f"        out: {av['devices'].get('output_name')}")
if not (av["tts"] and av["stt"]):
    print("\nCannot round-trip without both halves.")
    sys.exit(1)

# (phrase, what the parser should decide when it hears it)
#
# ⚠ THIS BLOCK CHANGED, AND THE OLD VERSION IS WORTH KNOWING ABOUT.
#   J-002 required the full phrase "yes go ahead" and asserted here that bare
#   "go ahead" must NOT approve, on the reasoning that a confirmation which
#   fires on a common idiom is not a confirmation. Sound reasoning; wrong in
#   practice. Mudit spoke to it and it refused "yeah", "sure", "ok" and "do it"
#   in turn, and an assistant that only answers to a password is not safer, it
#   is just unusable — and an unusable gate gets worked around, which is worse
#   than a permissive one. J-038 widened the affirm list to how people
#   actually speak, so bare "go ahead" now approves ON PURPOSE.
#
#   What replaced the strictness, and what the rest of this file now guards:
#     - a question never approves, whatever words it contains
#     - a contrastive reversal ("yes, but not that one") never approves
#     - the retraction words cancel from anywhere in the sentence
#     - anything undecided goes to core/confirm.py, which cannot approve
#       unless it is sure, and timeout still cancels
CASES = [
    ("yes go ahead", "approve"),
    ("confirmed go ahead", "approve"),
    ("go ahead", "approve"),          # J-038: deliberate. See above.
    ("cancel", "cancel"),
    ("no stop", "cancel"),
    ("what is the weather like today", None),
]

print("\n== round trip: text -> Kokoro -> WAV -> Whisper -> text")
for i, (phrase, verdict) in enumerate(CASES):
    wav = OUT / f"case{i}.wav"
    t0 = time.perf_counter()
    made = voice.to_wav(phrase, wav)
    t_tts = time.perf_counter() - t0
    if not made:
        check(f"synth {phrase!r}", False, str(voice._state["why"].get("tts")))
        continue
    t0 = time.perf_counter()
    heard, conf = voice.transcribe_scored(wav)
    t_stt = time.perf_counter() - t0
    if verdict == "approve":
        # The gate is min_stt_confidence (0.75). A length-based score gave a
        # one-second "yes go ahead" 0.72 and refused it. Acoustic confidence
        # from Whisper must clear the bar for clean speech, or voice is dead.
        gate = float(POLICY.confirmation.get("min_stt_confidence", 0.75))
        check(f"    acoustic confidence {conf:.2f} clears the {gate} gate", conf >= gate)

    same = norm(heard) == norm(phrase)
    close = norm(phrase) in norm(heard) or norm(heard) in norm(phrase)
    check(f"{phrase!r} -> {heard!r}", same or close,
          f"tts {t_tts:.1f}s stt {t_stt:.1f}s")

    if verdict:
        # The real question: does what Whisper ACTUALLY produced, punctuation
        # and capitalisation included, still parse to the right decision?
        got = POLICY.interpret_confirmation(heard, conf)     # the REAL confidence, not 1.0
        check(f"    ...and {heard!r} @ {conf:.2f} parses as {verdict}", got == verdict, f"got {got!r}")

print("\n== safety: an unclear answer must never approve")
# The last two are the ones that matter: an affirm phrase can appear inside a
# sentence that is plainly not an approval. Cancel wins ties for this reason.
for junk in ("", "hmm", "uh what", "maybe later", "yes but not that one",
             "no cancel that, yes go ahead with the other one",
             "don't go ahead", "why would you go ahead with that"):
    got = POLICY.interpret_confirmation(junk, 1.0)
    check(f"{junk!r} does not approve", got != "approve", f"got {got!r}")

print("\n== low confidence must never approve, whatever the words")
got = POLICY.interpret_confirmation("yes go ahead", 0.2)
check("'yes go ahead' at 0.2 confidence is not an approval", got != "approve", f"got {got!r}")

print("\n== what noise is actually allowed to do")
# ⚠ This block used to assert that "Let's go ahead.", "Confirms go ahead.",
#   "Yes, go head." and "Yes go a head" must NOT approve. They are what Whisper
#   really produced from approval phrases under noise (17b-calibrate-confidence),
#   and under the widened affirm list they now DO approve.
#
#   That is the correct outcome, and the old assertion was asking the wrong
#   question. Every one of those strings came from Mudit saying yes. Refusing
#   them does not prevent a wrong action; it prevents the RIGHT one, and makes
#   him say it again. The danger was never a mangled approval — it is noise
#   turning a refusal, a question or silence INTO an approval. That is what
#   this block asks now, and it is the question that actually protects him.
MANGLED = [
    ("cancer", "from 'cancel' under noise"),
    ("can sell that", "from 'cancel that'"),
    ("no, stop it", "a refusal, heard cleanly"),
    ("don't do that", "a refusal"),
    ("what is it going to do", "a question"),
    ("uh huh what", "noise"),
    ("the weather is nice", "unrelated speech"),
    ("yes but not that one", "an approval reversed mid-sentence"),
    ("why would you go ahead with that", "a challenge containing the phrase"),
]
for near, why in MANGLED:
    got = POLICY.interpret_confirmation(near, 0.6)
    check(f"{near!r} ({why}) does not approve", got != "approve", f"got {got!r}")

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
