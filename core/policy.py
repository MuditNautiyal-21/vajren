"""
The gate. Everything consequential passes through here.

Design rule: these decisions are made in CODE, never by asking a model.
A prompt can be talked out of a rule by text hidden in an email. An if-statement
cannot. This module is deliberately boring and deliberately not agent-writable.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "policy.yaml"

# How many words at the start of a reply carry the answer. Six covers
# "yes, please go ahead and ..." and "no, don't do that, instead ...".
# Beyond this the speaker is elaborating, not answering.
LEAD_WORDS = 6

# Words that can sit between an answer and a correction of that answer without
# counting as content. "yes, but no" is a person changing their mind mid-breath;
# "yes, go ahead, but don't take all day" is a person adding a condition.
FILLERS = {"well", "uh", "um", "er", "hmm", "oh", "sorry", "i", "mean",
           "and", "so", "then", "like", "just", "please", "okay", "ok"}

# A contrastive conjunction between an answer and a negative REVERSES the answer;
# any other joiner merely adds a condition to it. This is the whole difference
# between "yes, go ahead, and make sure it does not stay hidden" (an approval)
# and "yes, but not that one" (not an approval), and it is the only signal in
# the sentence that separates them without understanding what was said.
CONTRASTIVE = {"but", "though", "although", "however", "except", "unless",
               "actually"}

# Stronger than contrastive: these do not qualify an answer, they REPLACE the
# thing being answered about. "okay, but do the other file instead" is a yes to
# something that is not the action waiting. Nothing may approve on one of these.
REPLACEMENT = {"instead", "rather", "otherwise", "different"}

# An utterance that ASKS is not an utterance that ANSWERS. "Why would you go
# ahead with that" contains "go ahead" and means the opposite of it.
#
# ⚠ WH-WORDS ONLY. The first draft also listed the auxiliaries that open a
#   yes/no question — is, are, do, does, can, would. "do it" then opened with a
#   question word, and the single most common approval in the whole system
#   stopped working. Auxiliaries are ambiguous between question and imperative
#   ("do it", "will do", "can do"); wh-words are not. Whisper punctuates
#   questions, so "can you go ahead?" is caught by the question mark instead.
QUESTION_WORDS = {"what", "why", "how", "which", "where", "when", "who",
                  "whose", "whom"}


class Tier(str, Enum):
    AUTO = "auto"            # runs immediately
    CONFIRM = "confirm"      # speaks its plan, waits for the phrase
    FORBIDDEN = "forbidden"  # never, not even with confirmation


class PolicyViolation(Exception):
    """Raised before a tool runs. Never caught and retried — it means stop."""


@dataclass(frozen=True)
class Decision:
    tier: Tier
    reason: str
    lane: str  # "private" (local only) or "public" (may use free cloud tiers)


class Policy:
    def __init__(self, path: Path = POLICY_PATH) -> None:
        self._raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self._auto = set(self._raw.get("auto", []))
        self._confirm = set(self._raw.get("confirm", []))
        self._forbidden = set(self._raw.get("forbidden", []))
        self.confirm_once = set(self._raw.get("confirm_once_per_task", []))
        self._always_labels = [str(x).lower() for x in self._raw.get("always_confirm_labels", [])]
        self.never_trusted = set(self._raw.get("never_trusted", []))
        self._deny_paths = self._raw.get("denylist_paths", [])
        self._writable = [Path(p) for p in self._raw.get("writable_roots", [])]
        trig = self._raw.get("private_lane_triggers", {})
        self._private_sources = set(trig.get("sources", []))
        self._private_patterns = [re.compile(p) for p in trig.get("patterns", [])]
        self.confirmation = self._raw.get("confirmation", {})
        self.limits = self._raw.get("limits", {})

    # ---------------------------------------------------------------- tiers --
    def classify(self, tool: str, args: dict, sources: set[str] | None = None) -> Decision:
        lane = self.lane_for(args, sources or set())

        if tool in self._forbidden:
            return Decision(Tier.FORBIDDEN, f"'{tool}' is on the forbidden list", lane)

        # Unknown tools are NOT auto. Default-deny, always.
        if tool in self._auto:
            tier, reason = Tier.AUTO, "safe/reversible"
        elif tool in self._confirm:
            tier, reason = Tier.CONFIRM, "consequential"
        else:
            tier, reason = Tier.CONFIRM, f"'{tool}' is unclassified — defaulting to confirm"

        # ⚠ PRE-FLIGHT THE SHELL DENYLIST. Measured 2026-09-05, turn 14: Mudit
        #   said "shut down the PC". Vajren asked his permission, he approved,
        #   run_shell THEN refused it against its own denylist — twice. An
        #   approval is the most expensive thing this system can spend (10-15 s
        #   of his attention and a spoken exchange), and it was spent on a
        #   command that could never have run. Worse, the honest refusal
        #   arrived as "I kept describing it instead of doing it", which is not
        #   what happened.
        #
        #   The rule was always there; it was just enforced one layer too late.
        #   Checking it here makes the refusal immediate and truthful, and it
        #   is the SAME regex list — core.tools.shell.DENY — so there is no
        #   second copy to drift.
        if tool == "run_shell" and args.get("command"):
            from core.tools.shell import DENY
            for rx in DENY:
                if rx.search(str(args["command"])):
                    return Decision(
                        Tier.FORBIDDEN,
                        f"that command is on my own denylist ({rx.pattern}) in "
                        f"config/policy.yaml, so I can't run it even with your yes",
                        lane)

        # A path argument can escalate a normally-safe tool.
        for key in ("path", "file", "src", "dst", "directory"):
            if key in args and args[key]:
                self.assert_path_allowed(str(args[key]), write=(tier is not Tier.AUTO))

        return Decision(tier, reason, lane)

    # ----------------------------------------------------------------- lane --
    def lane_for(self, args: dict, sources: set[str]) -> str:
        """Which LiteLLM lane this task may use. Private = never leaves the box."""
        if sources & self._private_sources:
            return "private"
        blob = " ".join(str(v) for v in args.values())
        if any(p.search(blob) for p in self._private_patterns):
            return "private"
        return "public"

    def needs_fresh_confirmation(self, tool: str, args: dict) -> str:
        """
        Why this call must ask even if its tool was already granted for the
        request — or "" if the grant may stand. Decided on the LABEL of the
        thing about to be pressed; browser_click refuses to press an element
        whose real label does not match the one given, so this is checked
        against the truth, not the planner's description.
        """
        if tool not in ("browser_click", "browser_type", "app_click", "app_type"):
            return ""
        label = str(args.get("label", "")).lower()
        words = " " + re.sub(r"[^a-z0-9]+", " ", label) + " "
        for risky in self._always_labels:
            if f" {risky} " in words:
                return f"the button says {risky!r}"
        # ⚠ Enter in a chat box IS the Send button. The first WhatsApp message
        #   went out under the general "carry on" grant because app_type with
        #   submit=true never passed through the label check — the field was
        #   called "Type a message to …", not "Send". Anything that delivers
        #   words to another person asks every time, whichever key does it.
        if tool in ("browser_type", "app_type") and args.get("submit") and "search" not in label:
            for hint in ("message", "chat", "comment", "reply", "post", "tweet", "email", "mail"):
                if hint in label:
                    return "pressing enter here sends it to someone"
        return ""

    # Words that carry no instruction. A refusal made only of these is a
    # refusal; a refusal with anything else left over is a CORRECTION.
    # ⚠ Matched against _spoken(), which turns "don't" into "don t" - so the
    #   fragments are listed, not the contraction. "go ahead"/"go on" carry
    #   no target either; a refusal made of them is still a refusal.
    _EMPTY = {"no", "nope", "nah", "not", "dont", "don", "t", "won", "wont", "can", "cant",
              "isn", "doesn", "didn", "wouldn", "shouldn", "couldn", "s", "re", "ll", "ve",
              "do", "does", "did", "it", "its", "that", "this", "the", "a", "an", "one",
              "cancel", "stop", "wait", "hold", "on", "never", "mind", "forget", "leave",
              "now", "later", "yet", "please", "just", "um", "uh", "hmm", "okay", "ok",
              "actually", "well", "so", "and", "but", "i", "me", "you", "want", "wanted",
              "said", "asked", "to", "changed", "my", "rush", "hurry", "thanks", "thank",
              "go", "ahead", "proceed", "continue", "yes", "yeah", "yep", "sure", "fine"}
    _DEFER = re.compile(
        r"\b(let me|i want to (check|see|think|look|read)|i need to (check|see|think|look)|"
        r"hold on|hang on|one (sec|second|minute|moment)|give me a (sec|second|minute|moment)|"
        r"think about it|check (something|that|this|first)|in a (sec|second|minute|moment)|"
        r"not (yet|now|right now)|maybe later|come back to)\b", re.I)
    _DIRECTIVE = re.compile(
        r"\b(instead|other|different|rather|second|first|last|next|previous|use|click|open|"
        r"call|type|write|send|reply|search|go|make|pick|choose|select|try|the one|that one|"
        r"not that|not this|not him|not her|with|without|in|on|from|to)\b", re.I)

    def correction_in(self, heard: str) -> str:
        """
        The instruction inside a refusal, or "" if it is a bare refusal.

        ⚠ Mudit, 2026-09-06: "when it's asking for permission for a wrong task
          and I want to correct it at that step, it cancels the task and I have
          to start from scratch." A correction is not a refusal. "No, the other
          Sakshi" is NEW INSTRUCTION with everything already done kept; "no" is
          a refusal. The line between them: is anything left after the words
          that carry no instruction are removed?

        Deterministic, no model: a model was talked into approving "okay but
        do the other file" once already (see confirm.py). Approval never gets
        easier; but a correction is not an approval of anything - it re-plans,
        and the new step goes through the gate like any other.
        """
        h = self._spoken(heard)
        # A DEFERRAL is not a correction: he wants a moment, not a different
        # target. "stop, I want to check something first" -> cancel.
        if self._DEFER.search(h):
            return ""
        # An explicit retraction word wins unless a directive is ALSO there.
        # "well I suppose that looks right, actually cancel that" -> cancel;
        # "cancel that, do the other one instead" -> correction.
        if any(self._spoken(r) in h for r in self.confirmation.get("retractions", [])) \
                and not self._DIRECTIVE.search(h):
            return ""
        words = [w for w in re.split(r"[^a-z0-9']+", h) if w]
        content = [w for w in words if w not in self._EMPTY]
        if len(content) >= 2 or (content and self._DIRECTIVE.search(h)):
            return heard.strip()
        return ""

    def request_covers(self, request: str, tool: str, args: dict, history: list) -> str:
        """
        Does the spoken REQUEST itself already authorise this risky press?
        Returns the reason it does ("" if it does not).

        ⚠ Mudit, 2026-09-06: "whenever I say call somebody on WhatsApp, why
          does it ask me to confirm every time for every other person? The
          action is the same — it's call! Nothing's new!" He is right. 'call'
          sits on always_confirm_labels, so the gate asked even when he had
          just said, out loud, "call Mudit India". The gate exists to catch
          what he did NOT ask for; re-asking for the exact thing he named is
          friction, not safety.

          Scoped deliberately to CALLS. A call is fully specified by the
          request: the verb and the person are both in the sentence, and the
          chat that was opened (the last non-call app_click) can be checked
          against the name he said. A SEND is not fully specified — the
          message text was composed by the planner and he has not seen it —
          so sends, deletes, payments keep asking. A call to a person he did
          not name, or a call he never mentioned, also still asks.
        """
        if tool != "app_click":
            return ""
        label = str(args.get("label", "")).lower()
        if not any(k in label for k in ("call",)):
            return ""
        req = re.sub(r"[^a-z0-9 ]+", " ", (request or "").lower())
        if " call" not in " " + req and "call " not in req:
            return ""                                   # he never said "call"
        # Which chat is open? The last app_click that is a chat ITEM, not the
        # bare call BUTTON. A chat item's label is long and carries the name
        # ('Mudit India 2:33 AM Voice call Pinned chat' — the preview text
        # may itself say "Voice call"); the button is ≤3 words ('Voice call').
        target = ""
        for h in reversed(history or []):
            if h.get("tool") == "app_click":
                lab = str((h.get("args") or {}).get("label", "")).lower()
                if "call" in lab and len(lab.split()) <= 3:
                    continue                            # that's the call button itself
                target = lab
                break
        if not target:
            return ""
        # A name token from the opened chat must appear in what he said.
        # Action/UI words are never a person's name — without this, the chat
        # preview 'Voice call' matched the request word 'call' and would have
        # let "call Sakshi" ride while Mudit's chat was the one open.
        NOISE = {"unread", "message", "messages", "pinned", "chat", "am", "pm",
                 "call", "voice", "video", "missed", "incoming", "outgoing",
                 "you", "the", "and", "new", "typing", "online", "last", "seen"}
        toks = [t for t in re.split(r"[^a-z0-9]+", target) if len(t) > 2 and t not in NOISE]
        named = [t for t in toks if f" {t} " in f" {req} "]
        if not named:
            return ""
        return f"you asked me to call {' '.join(named)}"

    # ---------------------------------------------------------------- paths --
    def assert_path_allowed(self, path_str: str, *, write: bool) -> Path:
        p = Path(path_str).resolve()
        s = str(p)

        for pattern in self._deny_paths:
            if fnmatch.fnmatch(s, pattern) or s.lower().startswith(pattern.lower()):
                raise PolicyViolation(f"denylisted path: {s}")

        if write and not any(self._is_within(p, root) for root in self._writable):
            raise PolicyViolation(
                f"write outside writable_roots: {s}\n"
                f"Add it to config/policy.yaml deliberately if you meant to."
            )
        return p

    @staticmethod
    def _is_within(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    # -------------------------------------------------- voice confirmation --
    @staticmethod
    def _spoken(s: str) -> str:
        """
        Normalise a heard phrase for matching.

        ⚠ This exists because Whisper punctuates. It transcribes a spoken
        "yes go ahead" as "Yes, go ahead." — and a plain substring test against
        the phrase list then FAILS on the comma, returning 'unclear', which the
        gate treats as a cancel. Every spoken approval would have silently
        become a refusal and Vajren would have looked broken while being
        perfectly safe. Caught by scripts/17-voice-roundtrip.py, which runs real
        synthesized audio through real transcription rather than testing the
        parser against strings a human typed.
        """
        return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip() + " "

    def interpret_confirmation(self, heard: str, confidence: float) -> str:
        """
        Returns 'approve' | 'cancel' | 'unclear'. Ambiguity is never approval.
        This is the FAST, deterministic path; core.confirm handles the rest.

        Order of defence, and the order matters:
          1. confidence floor       — near-silence garbage
          2. LEAD WINDOW            — only the first LEAD_WORDS words are read
                                      as the answer; the rest is commentary
          3. negation / cancel in the lead   — cancel always wins a tie
          4. affirm in the lead
          5. cancel phrase ANYWHERE — a late "actually, cancel that" is honoured
        Step 3 is what lets the affirm list contain short, natural words like
        "yes", "sure" and "go ahead" without them firing inside a refusal.
        Step 2 is what stops a negation in the *elaboration* from overturning
        an answer already given — see the note in the body.
        """
        if confidence < float(self.confirmation.get("min_stt_confidence", 0.35)):
            return "unclear"
        words = self._spoken(heard).split()
        if not words:
            return "unclear"

        # A question is never an approval, however many approving words it
        # happens to contain. "Why would you go ahead with that" is a challenge.
        # Checked before anything else because it disqualifies the whole
        # utterance rather than competing with the words inside it.
        if "?" in heard or words[0] in QUESTION_WORDS:
            return "unclear"

        c = self.confirmation
        affirms = c.get("affirm_phrases", [])
        stops = list(c.get("negations", [])) + list(c.get("cancel_phrases", []))

        # A retraction is a word whose whole job is to take something back.
        # Those are honoured anywhere in the sentence, however late. Short
        # negatives like "no" and "not" are NOT retractions — they appear
        # constantly inside ordinary elaboration ("and no rush", "does not
        # stay hidden") and reading them as retractions is exactly the bug
        # this function was rewritten to kill.
        for phrase in c.get("retractions", []):
            if self._at(words, phrase) is not None:
                return "cancel"

        lead = words[:LEAD_WORDS]
        aff = self._first(lead, affirms)
        stop = self._first(lead, stops)

        # ⚠ ORDER decides, not presence. People answer first and elaborate
        #   afterwards. "Yes, please go ahead and make sure it does not stay
        #   hidden" is an approval with a condition attached; scanning the whole
        #   sentence for "not" cancelled it out loud, which is worse than the
        #   deafness it replaced. Whatever comes FIRST is the answer.
        if stop is not None and (aff is None or stop[0] < aff[0]):
            return "cancel"
        if aff is None:
            return "unclear"

        # An affirmative answered first. Two things can still take it back, and
        # neither is a clean cancel, so both go back for a re-ask rather than
        # being guessed at:
        #
        #   REVERSAL   "yes, but not that one" — a contrastive conjunction
        #              between the answer and a negative. Contrast with
        #              "yes, go ahead, AND make sure it does not stay hidden",
        #              where the joiner adds a condition instead of removing
        #              the answer. The conjunction is the whole difference.
        #   TRAILING   "yeah, no" — a bare negative at the very end, with only
        #              filler before it. A person changing their mind mid-breath.
        tail_start = max((m[1] for m in self._all(lead, affirms) if m[1] <= len(words)),
                         default=aff[1])
        late = self._first(words[tail_start:], stops)
        if late is not None:
            gap = words[tail_start:tail_start + late[0]]
            after = words[tail_start + late[1]:]
            if any(w in CONTRASTIVE for w in gap):
                return "unclear"
            if all(w in FILLERS for w in gap) and all(w in FILLERS for w in after):
                return "unclear"
        if any(w in REPLACEMENT for w in words[tail_start:]):
            return "unclear"
        return "approve"

    # -- phrase location, in words, so that order can be compared -------------
    @staticmethod
    def _at(words: list[str], phrase: str) -> tuple[int, int] | None:
        """Earliest (start, end) word index of `phrase` in `words`, or None."""
        pw = re.sub(r"[^a-z0-9]+", " ", phrase.lower()).split()
        if not pw:
            return None
        for i in range(len(words) - len(pw) + 1):
            if words[i:i + len(pw)] == pw:
                return (i, i + len(pw))
        return None

    @classmethod
    def _all(cls, words: list[str], phrases: list[str]) -> list[tuple[int, int]]:
        return [m for m in (cls._at(words, p) for p in phrases) if m is not None]

    @classmethod
    def _first(cls, words: list[str], phrases: list[str]) -> tuple[int, int] | None:
        hits = cls._all(words, phrases)
        return min(hits) if hits else None


    def is_taken_back(self, heard: str) -> bool:
        """
        True when an affirmative was given and then WITHDRAWN or REDIRECTED in
        the same breath — "yeah, no", "yes, but no", "okay, but the other one
        instead".

        This exists because 'unclear' has two very different causes, and only
        one of them is safe to hand to a model. "Hmm" is unclear because
        nothing decisive was said; "yeah, no" is unclear because something
        decisive was said TWICE, in opposite directions. The reflex model,
        shown "yeah, no", reads the "yeah" and approves. So the deterministic
        layer keeps the veto on this shape: the model may resolve an absence of
        evidence, never a contradiction.
        """
        words = self._spoken(heard).split()
        if not words:
            return False
        affirms = self.confirmation.get("affirm_phrases", [])
        stops = list(self.confirmation.get("negations", [])) + \
            list(self.confirmation.get("cancel_phrases", []))
        lead = words[:LEAD_WORDS]
        aff = self._first(lead, affirms)
        if aff is None:
            return False
        tail_start = max((m[1] for m in self._all(lead, affirms) if m[1] <= len(words)),
                         default=aff[1])
        if any(w in REPLACEMENT for w in words[tail_start:]):
            return True
        late = self._first(words[tail_start:], stops)
        if late is None:
            return False
        gap = words[tail_start:tail_start + late[0]]
        after = words[tail_start + late[1]:]
        # A contrastive between the yes and the negative. This DELIBERATELY
        # catches both "yes, but not that one" (a correction) and "yes go
        # ahead, but don't take too long" (a condition), because nothing here
        # can reliably tell them apart — and the reflex model, asked to, got
        # "okay, but do the other file instead" wrong by approving it. So both
        # are re-asked. The cost is one extra round trip on a phrasing that is
        # unambiguous the second time; the alternative is running the wrong
        # action because a 4B model liked the word "okay".
        if any(w in CONTRASTIVE for w in gap):
            return True
        return all(w in FILLERS for w in gap) and all(w in FILLERS for w in after)


POLICY = Policy()
