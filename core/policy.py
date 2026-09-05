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
          1. confidence floor  — near-silence garbage
          2. NEGATION          — "don't go ahead" contains "go ahead"
          3. cancel phrases    — cancel always wins a tie
          4. affirm phrases
        Step 2 is what lets the affirm list contain short, natural words like
        "yes", "sure" and "go ahead" without them firing inside a refusal.
        """
        if confidence < float(self.confirmation.get("min_stt_confidence", 0.35)):
            return "unclear"
        h = self._spoken(heard)
        if not h.strip():
            return "unclear"
        if any(self._spoken(n) in h for n in self.confirmation.get("negations", [])):
            return "cancel"
        if any(self._spoken(p) in h for p in self.confirmation.get("cancel_phrases", [])):
            return "cancel"
        if any(self._spoken(p) in h for p in self.confirmation.get("affirm_phrases", [])):
            return "approve"
        return "unclear"


POLICY = Policy()
