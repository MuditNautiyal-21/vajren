"""
Memory tools — what Mudit can ask Vajren to keep, and to drop.

Both auto tier: remembering something he said out loud is not consequential,
and forgetting is reversible (facts are superseded, not deleted). What is
NOT here: any tool that lets a file or page write a fact. See core/memory.py.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from core import memory
from core.tools import tool


class Remember(BaseModel):
    fact: str = Field(description="one plain sentence, e.g. 'His surname is spelled Nautiyal' "
                                  "or 'The PCYT Chrome profile is his personal one'")
    subject: str = Field(default="mudit", description="mudit | machine | files | preferences")


@tool("remember_fact", Remember)
def remember_fact(fact: str, subject: str = "mudit") -> dict:
    """Keep a durable fact Mudit stated. Not for file or page contents."""
    return memory.remember(fact, subject=subject, source="stated")


class Recall(BaseModel):
    query: str = Field(description="what you are trying to remember, in a few words")


@tool("recall", Recall)
def recall(query: str) -> dict:
    """Search remembered facts and past requests."""
    facts = memory.recall(query)
    turns = memory.related_turns(query, n=4)
    return {"facts": [f["fact"] for f in facts],
            "past": [{"when": t["at"][:16], "asked": t["request"][:160], "result": t["outcome"][:160]}
                     for t in turns],
            "count": len(facts) + len(turns)}


class Forget(BaseModel):
    about: str = Field(description="words identifying the fact to drop")


@tool("forget_fact", Forget)
def forget_fact(about: str) -> dict:
    """Drop a remembered fact Mudit says is wrong or no longer true."""
    return memory.forget(about)
