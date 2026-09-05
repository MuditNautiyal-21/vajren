"""
Understanding an answer at the approval gate.

THE PROBLEM THIS SOLVES: the gate used to accept three exact phrases. Say
anything else — "yeah do it", "what's in that file?", "hang on, which one?" —
and it replied "Sorry, say 'yes go ahead' or 'cancel'." Forever. It felt deaf,
and being deaf is not the same as being safe.

THE SHAPE OF THE FIX, and the order is the whole design:

  1. POLICY.interpret_confirmation — deterministic phrase + negation match.
     Handles almost everything, in microseconds, with no model involved.
  2. Only if that is 'unclear': ask the reflex model (4B, CPU, ~1-2 s) to
     classify. Short prompt, so this is the one job J-031 says it is good at.
  3. A model 'approve' is then RE-CHECKED against the retraction list. A model
     may be talked into a yes; an if-statement cannot.

Anything still unresolved is a question, not an approval — and it gets an
actual answer rather than the magic-phrase line repeated back.

⚠ The model may only ever move an answer from 'unclear'. It cannot overturn a
  deterministic cancel, and it cannot manufacture an approval out of a
  sentence containing a negation. Approval is the one decision in this system
  that never gets easier to obtain.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.llm import structured
from core.policy import POLICY


class GateAnswer(BaseModel):
    """What the person meant, and what to say if they meant neither."""
    decision: Literal["approve", "cancel", "neither"] = Field(
        description="approve only if they clearly agreed to the action proceeding NOW")
    reply: str = Field(
        default="",
        description="if neither: one short spoken sentence answering what they actually "
                    "said, then asking again whether to go ahead. Empty otherwise.")


SYSTEM = """You decide what a person meant when asked to approve an action.

You are given the action that is waiting, and what the person said back.
Choose exactly one:
  approve  - they clearly agreed to it happening now
  cancel   - they refused, deferred, hesitated, or asked to wait
  neither  - they asked a question, changed the subject, or said something
             that is not an answer

Rules:
- Hesitation is NOT approval. "I guess", "maybe", "if you think so" -> cancel.
- A question is never approval, even an enthusiastic one.
- If you are not certain they agreed, you are not choosing approve.

- If they agreed to something OTHER than the action above - a different file,
  a different place, "the other one" - that is not approval of this action.
  Choose cancel, so a new one can be proposed.
For 'neither', write `reply`: answer their question in one plain sentence using
only the action description you were given, then ask again whether to go ahead.
Never invent details you were not told."""


def resolve(heard: str, confidence: float, pending_speak: str) -> tuple[str, str]:
    """
    (decision, reply) where decision is 'approve' | 'cancel' | 'neither'.

    `reply` is what to say out loud when the answer was neither — already
    phrased to answer them and re-ask.
    """
    fast = POLICY.interpret_confirmation(heard, confidence)
    if fast in ("approve", "cancel"):
        return fast, ""

    if not heard.strip():
        return "cancel", ""

    # ⚠ A yes with something walked back after it goes straight to a re-ask,
    #   without the model, and this is deliberate on BOTH sides.
    #
    #   It must not approve: "okay, but do the other file instead" is a yes to
    #   an action that is not the one waiting, and the reflex model approved
    #   exactly that when asked.
    #
    #   It must not cancel either, and that is the half worth defending.
    #   "Yes go ahead, but don't take too long" is a person saying YES. Letting
    #   the model call it — it called it cancel — reproduces the original bug
    #   in this file's own words: a clear spoken approval refused because of a
    #   negative in the elaboration. Neither answer is safe to guess, so it
    #   does not guess. It asks. He says "yes" and it costs three seconds.
    if POLICY.is_taken_back(heard):
        return "neither", "Sorry — was that a yes for this one?"

    try:
        out = structured(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content":
                 f"ACTION WAITING:\n{pending_speak}\n\nTHEY SAID:\n{heard}"}],
            GateAnswer, lane="reflex")
    except Exception:                                          # noqa: BLE001
        # The classifier is an accelerator, never a dependency. If it is down,
        # an unrecognised answer is still not an approval.
        return "neither", "Sorry, I didn't catch that. Yes or no?"

    if out.decision == "approve":
        # ⚠ The model does not get the last word on approval — but the check
        #   that enforces that had the SAME bug as the parser it was guarding.
        #   It scanned the whole sentence for any negation, so "yes go ahead,
        #   but don't take too long" was overturned to cancel by the word
        #   "don't" sitting in a condition. Fixing the parser alone left this
        #   copy of the mistake one layer down, which is how J-029 and J-035
        #   went too.
        #
        #   What must remain true is narrower than "no negatives anywhere":
        #     - the model may only ever move an answer that the deterministic
        #       parser left UNCLEAR. It cannot overturn a `cancel` — that is
        #       already guaranteed above, since a fast cancel returns early.
        #     - a retraction word anywhere still wins. "cancel", "stop",
        #       "never mind" mean one thing, and no reading of intent gets to
        #       argue with them.
        h = POLICY._spoken(heard)
        if any(POLICY._spoken(r) in h for r in POLICY.confirmation.get("retractions", [])):
            return "cancel", ""
        return "approve", ""

    if out.decision == "cancel":
        return "cancel", ""

    return "neither", (out.reply.strip() or "Sorry — yes or no?")
