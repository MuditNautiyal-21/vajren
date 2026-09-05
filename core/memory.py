"""
What Vajren carries from one conversation to the next.

Three kinds, one SQLite file (memory/vajren.db, schema in memory/schema.sql):

  EPISODIC   turns — each request, what was said back, what tools ran.
             The last few are loaded at startup so "do that again" works
             after a restart; the rest are searched by keyword on demand.
  SEMANTIC   facts — durable things about Mudit and this machine. Written by
             him ("remember that…") or distilled from a turn by the reflex
             model in the background, after the answer has been spoken so it
             costs no latency. Superseded, never deleted.
  TRUST      which shapes of action he has approved often enough that asking
             again is noise. Earned slowly (three in a row), lost at once
             (one cancel), announced when granted, revocable by voice.

WHERE IT LIVES, since he asked: on C:, next to everything else, and bounded.
A few megabytes of SQLite on an internal SSD is not what slows a PC. The
models are 45 GB and they stay on C: precisely so they are NOT read off the
USB drive on every swap. `prune()` keeps turns to `RETENTION_DAYS`; facts are
small by nature. `VAJREN_DATA` can move the whole tree if he ever wants it to.

WHAT MAY BE REMEMBERED: what Mudit said, and what Vajren did. Never the
contents of a file, a page, or a command's output — that is untrusted, and a
fact planted by a web page would be an instruction with a very long fuse.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from core.tools import DB, SCHEMA

RETENTION_DAYS = 120
RECENT_TURNS = 6          # carried into a new session as context
RECALL_TURNS = 3          # related past turns shown for a request
RECALL_FACTS = 8          # related facts shown for a request
TRUST_AFTER = 3           # consecutive approvals before a shape stops asking


def _con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def _fts(q: str) -> str:
    """A query FTS5 will not choke on: bare words, OR'd, stopwords dropped."""
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", q.lower())
             if w not in _STOP][:12]
    return " OR ".join(f'"{w}"' for w in words)


_STOP = set("the and for you your that this with from into open want can please just "
            "then also about what which there here have has had was were are its it's "
            "vajren vajran hey okay yeah yes now like make sure".split())


# ------------------------------------------------------------- episodic --
def record_turn(session_id: str, episode_id: int | None, request: str,
                outcome: str, tools: list[str], status: str) -> int:
    with _con() as con:
        # Local time, explicitly. The column default is datetime('now'), which
        # is UTC, and "earlier · 10:59" on a screen whose clock says 07:08 reads
        # as a bug to the person looking at it.
        cur = con.execute(
            "INSERT INTO turns (at, session_id, episode_id, request, outcome, tools, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(sep=" ", timespec="seconds"), session_id, episode_id,
             request, outcome, " ".join(tools), status))
        return int(cur.lastrowid)


def recent_turns(n: int = RECENT_TURNS) -> list[dict]:
    """The last n turns from ANY session, oldest first — the thread to pick up."""
    with _con() as con:
        rows = con.execute("SELECT at, request, outcome, tools, status FROM turns "
                           "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def related_turns(request: str, n: int = RECALL_TURNS, exclude_session: str = "") -> list[dict]:
    q = _fts(request)
    if not q:
        return []
    with _con() as con:
        rows = con.execute(
            "SELECT t.at, t.request, t.outcome, t.tools, t.status FROM turns_fts f "
            "JOIN turns t ON t.id = f.rowid WHERE turns_fts MATCH ? AND t.session_id != ? "
            "ORDER BY bm25(turns_fts) LIMIT ?", (q, exclude_session, n)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------- semantic --
def remember(fact: str, subject: str = "mudit", source: str = "stated") -> dict:
    fact = re.sub(r"\s+", " ", fact).strip().rstrip(".")
    if len(fact) < 4:
        return {"error": "that is not a fact"}
    with _con() as con:
        # The same fact again is not a second fact; a contradicting fact on the
        # same subject is handled by whoever recalls (newest first).
        row = con.execute("SELECT id FROM facts WHERE subject=? AND fact=? AND valid_to IS NULL",
                          (subject, fact)).fetchone()
        if row:
            return {"fact": fact, "subject": subject, "already_known": True, "id": row["id"]}
        cur = con.execute("INSERT OR IGNORE INTO facts (subject, fact, source) VALUES (?,?,?)",
                          (subject, fact, source))
        return {"fact": fact, "subject": subject, "already_known": False, "id": cur.lastrowid}


def forget(fact_query: str) -> dict:
    q = _fts(fact_query)
    with _con() as con:
        rows = con.execute("SELECT f.id, f.fact FROM facts_fts x JOIN facts f ON f.id = x.rowid "
                           "WHERE facts_fts MATCH ? AND f.valid_to IS NULL ORDER BY bm25(facts_fts) LIMIT 3",
                           (q,)).fetchall() if q else []
        if not rows:
            return {"forgot": [], "count": 0}
        con.execute(f"UPDATE facts SET valid_to = datetime('now') WHERE id IN "
                    f"({','.join('?' * len(rows))})", [r["id"] for r in rows])
        return {"forgot": [r["fact"] for r in rows], "count": len(rows)}


def recall(query: str, n: int = RECALL_FACTS) -> list[dict]:
    q = _fts(query)
    with _con() as con:
        if q:
            rows = con.execute(
                "SELECT f.subject, f.fact, f.source, f.valid_from FROM facts_fts x "
                "JOIN facts f ON f.id = x.rowid WHERE facts_fts MATCH ? AND f.valid_to IS NULL "
                "AND f.subject != 'lessons' ORDER BY bm25(facts_fts), f.id DESC LIMIT ?", (q, n)).fetchall()
        else:
            rows = []
        if len(rows) < n:
            # Newest facts are worth showing even when nothing matched — they
            # are usually corrections to something that just went wrong.
            have = {r["fact"] for r in rows}
            more = con.execute("SELECT subject, fact, source, valid_from FROM facts "
                               "WHERE valid_to IS NULL AND subject != 'lessons' ORDER BY id DESC LIMIT ?", (n,)).fetchall()
            rows = list(rows) + [r for r in more if r["fact"] not in have][: n - len(rows)]
    return [dict(r) for r in rows]


def all_facts() -> list[dict]:
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, subject, fact, source, valid_from FROM facts WHERE valid_to IS NULL ORDER BY id")]


# ----------------------------------------------- distilling, in the background --
class _Distilled(BaseModel):
    facts: list[str] = Field(default_factory=list, description=(
        "0 to 3 durable facts about Mudit, his machine, his files or his preferences that "
        "this exchange established and that would still be true next month. Each one a "
        "short plain sentence. Nothing about what happened just now; nothing guessed."))


def distill_later(request: str, outcome: str, tools: list[str]) -> None:
    """Extract durable facts from a turn, off the hot path. Fire and forget."""
    def go():
        try:
            from core.llm import structured
            out = structured(
                [{"role": "system", "content":
                  "You keep a personal assistant's long-term memory. From one exchange, list "
                  "only what is DURABLE — names, spellings, where things are, what he prefers, "
                  "what his machine has. Not what was done today; not anything from a file or "
                  "web page; not guesses. Usually the answer is no facts at all."},
                 {"role": "user", "content":
                  f"HE SAID: {request}\nASSISTANT REPLIED: {outcome}\nTOOLS USED: {' '.join(tools) or 'none'}"}],
                _Distilled, lane="reflex")
            for f in out.facts[:3]:
                if 8 <= len(f) <= 200:
                    remember(f, source="observed")
        except Exception:                                          # noqa: BLE001
            pass                                                   # memory is best effort
    threading.Thread(target=go, daemon=True, name="vajren-distill").start()


# ---------------------------------------------------------------- trust --
def shape(tool: str, args: dict) -> str:
    """The arg with the specifics removed. Same shape → same decision."""
    a = args or {}
    if "path" in a and a.get("path"):
        return str(Path(str(a["path"])).parent).lower()
    if "url" in a and a.get("url"):
        return re.sub(r"^https?://(www\.)?", "", str(a["url"])).split("/")[0].lower()
    if "app" in a:
        return Path(str(a.get("app", ""))).name.lower()
    if "title" in a:
        return str(a.get("title", "")).lower()[:40]
    if "label" in a:
        return str(a.get("label", "")).lower()[:40]
    return "*"


def trust_record(tool: str, args: dict, approved: bool) -> dict:
    """Count an approval or a cancel. Returns the row, with `newly_granted`."""
    p = shape(tool, args)
    with _con() as con:
        row = con.execute("SELECT * FROM trust WHERE tool=? AND pattern=?", (tool, p)).fetchone()
        if row is None:
            con.execute("INSERT INTO trust (tool, pattern) VALUES (?,?)", (tool, p))
            row = con.execute("SELECT * FROM trust WHERE tool=? AND pattern=?", (tool, p)).fetchone()
        approvals = row["approvals"] + 1 if approved else 0
        cancels = row["cancels"] + (0 if approved else 1)
        granted = row["granted_at"]
        newly = False
        if not approved:
            granted = None            # trust is lost at once
        elif granted is None and approvals >= TRUST_AFTER:
            # A revoke is not a life sentence: "ask me about that again" means
            # go back to asking, and three more yeses earn it back the same
            # slow way. revoked_at stays as a record of when it happened.
            granted = datetime.now().isoformat(timespec="seconds")
            newly = True
        con.execute("UPDATE trust SET approvals=?, cancels=?, granted_at=?, last_at=datetime('now') "
                    "WHERE tool=? AND pattern=?", (approvals, cancels, granted, tool, p))
    return {"tool": tool, "pattern": p, "approvals": approvals, "granted": granted is not None,
            "newly_granted": newly}


def trusted(tool: str, args: dict) -> bool:
    with _con() as con:
        row = con.execute("SELECT granted_at, revoked_at FROM trust WHERE tool=? AND pattern=?",
                          (tool, shape(tool, args))).fetchone()
    return bool(row and row["granted_at"] and not row["revoked_at"])


def revoke(tool: str = "", pattern: str = "") -> dict:
    """'Ask me about that again.' Blank = everything."""
    with _con() as con:
        if tool:
            con.execute("UPDATE trust SET granted_at=NULL, approvals=0, revoked_at=datetime('now') "
                        "WHERE tool=? AND (pattern=? OR ?='')", (tool, pattern, pattern))
        else:
            con.execute("UPDATE trust SET granted_at=NULL, approvals=0, revoked_at=datetime('now') "
                        "WHERE granted_at IS NOT NULL")
        n = con.total_changes
    return {"revoked": n}


def trust_report() -> list[dict]:
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT tool, pattern, approvals, cancels, granted_at, revoked_at FROM trust "
            "ORDER BY granted_at IS NULL, tool, pattern")]


# ------------------------------------------------------------- lessons --
# The evolution loop, closed. The session audit (scripts/31-session-audit.py)
# knows what a bad turn looks like — repeated steps, a promise instead of a
# result, a cancel, a wrong window. The server scores each finished turn the
# same way and, when it was bad, files one line: what was asked, what went
# wrong, what to do instead. The planner sees the lessons for requests that
# look like the current one. No model writes these; they come from code that
# watched what happened, and they cost nothing at query time.
def record_lesson(request: str, fault: str, fix: str) -> dict:
    line = f"When asked {request[:90]!r}: {fault}. Next time: {fix}"
    return remember(line, subject="lessons", source="observed")


def lessons_for(request: str, n: int = 3) -> list[str]:
    q = _fts(request)
    if not q:
        return []
    with _con() as con:
        rows = con.execute(
            "SELECT f.fact FROM facts_fts x JOIN facts f ON f.id = x.rowid "
            "WHERE facts_fts MATCH ? AND f.subject = 'lessons' AND f.valid_to IS NULL "
            "ORDER BY bm25(facts_fts) LIMIT ?", (q, n)).fetchall()
    return [r["fact"].split(": ", 1)[1] if ": " in r["fact"] else r["fact"] for r in rows]


# ------------------------------------------------------------- upkeep --
def prune() -> dict:
    with _con() as con:
        n = con.execute("DELETE FROM turns WHERE at < datetime('now', ?)",
                        (f"-{RETENTION_DAYS} days",)).rowcount
        con.execute("DELETE FROM facts WHERE valid_to IS NOT NULL AND valid_to < datetime('now', '-365 days')")
    return {"pruned_turns": n, "db_bytes": DB.stat().st_size if DB.exists() else 0}


def stats() -> dict:
    with _con() as con:
        t = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        f = con.execute("SELECT COUNT(*) FROM facts WHERE valid_to IS NULL").fetchone()[0]
        g = con.execute("SELECT COUNT(*) FROM trust WHERE granted_at IS NOT NULL").fetchone()[0]
    return {"turns": t, "facts": f, "trusted_shapes": g,
            "db_mb": round((DB.stat().st_size if DB.exists() else 0) / 1e6, 2)}
