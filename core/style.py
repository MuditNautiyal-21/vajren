"""
Style guard for the writer lane.

Nobody has published a per-model study of which open model uses the fewest
em-dashes, so this does not try to pick a model by that. It does the deterministic
half instead: catch the tells in the OUTPUT, before you ever see the draft.

Two parts:
  1. A hard tell-detector (regex, no model). Runs on every writer-lane output.
  2. A voice profile loaded from config/voice.md — real sentences you actually
     wrote, used as few-shot examples. This does more for "sounds like me" than
     any amount of prompt instruction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "config" / "voice.md"

# Phrases that mark a draft as machine-written to anyone who reads a lot of email.
TELLS: dict[str, re.Pattern] = {
    "delve": re.compile(r"\bdelve[sd]?\b", re.I),
    "tapestry": re.compile(r"\b(tapestry|landscape of|realm of)\b", re.I),
    "not_only_but": re.compile(r"\bnot only\b.{0,80}\bbut also\b", re.I | re.S),
    "its_worth_noting": re.compile(r"\b(it'?s worth noting|it is important to note)\b", re.I),
    "dive_in": re.compile(r"\b(let'?s dive in|deep dive into)\b", re.I),
    "in_todays": re.compile(r"\bin today'?s (fast-paced|ever-evolving|digital)\b", re.I),
    "leverage_verb": re.compile(r"\bleverage(s|d)?\b(?! ratio)", re.I),
    "seamless": re.compile(r"\b(seamless(ly)?|robust|cutting-edge|game-?chang\w+)\b", re.I),
    "i_hope_this": re.compile(r"\bI hope this (email|message) finds you well\b", re.I),
    "reaching_out": re.compile(r"\bI'?m reaching out to\b", re.I),
    "excited_to": re.compile(r"\b(thrilled|excited) to (share|announce)\b", re.I),
    "em_dash_spam": re.compile(r"(—[^—]{0,120}){3,}"),          # 3+ em dashes in a row
    "rule_of_three": re.compile(r"\b\w+, \w+, and \w+\b.{0,200}\b\w+, \w+, and \w+\b", re.S),
    "hedge_stack": re.compile(r"\b(that said|however|moreover|furthermore|additionally)\b", re.I),
    "closing_fluff": re.compile(r"\b(please don'?t hesitate to|feel free to reach out)\b", re.I),
    "emoji_bullets": re.compile(r"^\s*[\U0001F300-\U0001FAFF]", re.M),
}

# LinkedIn-post-specific tells. Different register, different crimes.
POST_TELLS: dict[str, re.Pattern] = {
    "one_line_paragraphs": re.compile(r"(?:^.{1,60}\n\n){5,}", re.M),
    "hook_colon": re.compile(r"^(Here'?s|The truth|Unpopular opinion|Hot take)\b.*:", re.I | re.M),
    "hashtag_pile": re.compile(r"(#\w+\s*){4,}"),
    "agree_question": re.compile(r"\b(Agree\?|Thoughts\?|What do you think\?)\s*$", re.I | re.M),
}


@dataclass
class StyleReport:
    hits: dict[str, list[str]]
    em_dash_count: int
    avg_sentence_words: float
    passed: bool

    def summary(self) -> str:
        if self.passed:
            return "clean"
        return "; ".join(f"{k} ({len(v)})" for k, v in self.hits.items())


def check(text: str, *, kind: str = "email") -> StyleReport:
    patterns = dict(TELLS)
    if kind in ("post", "linkedin"):
        patterns.update(POST_TELLS)

    hits = {name: p.findall(text) for name, p in patterns.items()}
    hits = {k: v for k, v in hits.items() if v}

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    avg = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)

    return StyleReport(
        hits=hits,
        em_dash_count=text.count("—"),
        avg_sentence_words=round(avg, 1),
        passed=not hits and text.count("—") <= 2,
    )


def voice_examples(n: int = 6) -> str:
    """
    Few-shot from things Mudit actually wrote. Paste real messages into
    config/voice.md, separated by '---'. This beats any prompt instruction.
    """
    if not VOICE.exists():
        return ""
    chunks = [c.strip() for c in VOICE.read_text(encoding="utf-8").split("\n---\n") if c.strip()]
    return "\n\n".join(chunks[:n])


WRITER_SYSTEM = """You are drafting as Mudit, in his voice, for him to send as himself.

Rules, in priority order:
1. Say the thing. First sentence carries the point, not a windup.
2. Short sentences. Vary the length. Read it aloud in your head — if you would not
   say it out loud to this person, rewrite it.
3. No "I hope this finds you well", no "I'm reaching out to", no "delve", no
   "leverage", no "seamless", no "excited to share", no closing fluff.
4. At most two em dashes in the whole message. Prefer a full stop.
5. No three-item lists unless there are genuinely three things.
6. Specific over impressive. A real detail beats an adjective.
7. Match the recipient's register. A recruiter, a professor and a friend get three
   different messages.
8. Never invent a fact, a date, a shared history, or a compliment about their work
   that you have not actually read.

{voice}
"""


def system_prompt() -> str:
    ex = voice_examples()
    return WRITER_SYSTEM.format(
        voice=("\nHere is how Mudit actually writes. Match this:\n\n" + ex) if ex else ""
    )
