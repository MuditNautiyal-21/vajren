"""
The part of the memory that CONNECTS things.

core/memory.py stores what happened (turns), what is true (facts) and what may
run unasked (trust). All of it is flat: a fact is a string, and two facts about
the same person have nothing to do with each other. This module adds the shape
a brain actually has — things, and the links between them — and the two
operations that make it grow on its own:

  ENCODE       after every step, the things that step touched become nodes, and
               everything touched in one turn becomes linked to everything else.
  CONSOLIDATE  off the hot path, later: merge the duplicates, promote what has
               been seen enough times into a fact, dedupe the lessons pile,
               decay what stopped being used, drop what decayed to nothing.

Recall is then SPREADING ACTIVATION, not keyword match: seed from the words in
the request, walk one or two hops out along the strongest links, and return the
neighbourhood that lit up. "message Sakshi" reaches WhatsApp without anyone
having written "Sakshi is on WhatsApp" anywhere, because the two have been
touched together four times.

⚠ WHAT MAY BECOME A BELIEF, enforced here in code and not in a prompt.
  A memory that grows by itself is a prompt-injection sink with a long fuse:
  poison one page, and a sentence out of it becomes something Vajren believes
  and acts on for months. core/memory.py already says this in its docstring;
  saying it is not enforcing it. So:

      stated    Mudit said it                                 -> believable
      observed  Vajren did it and verify.py passed            -> believable
      inferred  consolidation saw the same thing N times      -> believable
      read      out of a file, a page, or a command's stdout  -> NEVER asserted

  `read` rows are kept — they are the record of what a source claimed — but
  BELIEVABLE is the only set that reaches the planner, and remember_read()
  cannot write anything else. This closes the hole the code review left open:
  "nothing in code stops page content from becoming a remembered fact."

⚠ ENTITIES COME FROM ARGUMENTS, NEVER FROM RESULTS. A tool's arguments are
  Vajren's own decision, already through the gate. A tool's RESULT is whatever
  the world said back — a page title, a chat row, a filename on someone else's
  server. Extracting nodes from results would let a web page name the things in
  Mudit's brain. Extraction reads args and the request he spoke, full stop.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from core.tools import DB, SCHEMA

BELIEVABLE = ("stated", "observed", "inferred", "corrected")
READ_ONLY_PROV = "read"

HOPS = 2                  # how far activation spreads
DECAY_PER_HOP = 0.45      # a neighbour is worth less than the thing itself
PROMOTE_AFTER = 4         # observations of one pair before it becomes a fact
EDGE_FLOOR = 0.15         # below this an edge is forgotten
MAX_SEEDS = 6
MAX_NODES = 14

_STOP = set("the and for you your that this with from into open want can please just "
            "then also about what which there here have has had was were are its it's "
            "vajren vajran hey okay yeah yes now like make sure send message tell ask "
            "get put set find show look give take let all any one two some more".split())


def _con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


# ------------------------------------------------------------------ nodes --
def touch(kind: str, display: str, con: sqlite3.Connection | None = None) -> int | None:
    """Upsert one thing and count the meeting. Returns its id."""
    display = re.sub(r"\s+", " ", str(display or "")).strip()
    if len(display) < 2 or len(display) > 120:
        return None
    name = display.lower()
    own = con is None
    con = con or _con()
    try:
        row = con.execute("SELECT id, merged_into FROM entities WHERE kind=? AND name=?",
                          (kind, name)).fetchone()
        if row:
            eid = row["merged_into"] or row["id"]
            con.execute("UPDATE entities SET mentions = mentions + 1, last_seen = ? WHERE id = ?",
                        (_now(), eid))
            return int(eid)
        cur = con.execute(
            "INSERT INTO entities (kind, name, display, first_seen, last_seen) VALUES (?,?,?,?,?)",
            (kind, name, display, _now(), _now()))
        return int(cur.lastrowid)
    finally:
        if own:
            con.commit()
            con.close()


def link(a: int, b: int, rel: str = "with", con: sqlite3.Connection | None = None) -> None:
    """Strengthen the link between two things. Undirected: stored both ways."""
    if not a or not b or a == b:
        return
    own = con is None
    con = con or _con()
    try:
        for s, d in ((a, b), (b, a)):
            row = con.execute("SELECT id, weight, evidence FROM edges WHERE src=? AND dst=? AND rel=?",
                              (s, d, rel)).fetchone()
            if row:
                # Diminishing returns: the tenth time two things co-occur says
                # much less than the second. Without this, one repetitive task
                # would dominate every recall for months.
                con.execute("UPDATE edges SET weight = MIN(weight + 1.0 / (evidence + 1), 8.0), "
                            "evidence = evidence + 1, last_seen = ? WHERE id = ?", (_now(), row["id"]))
            else:
                con.execute("INSERT INTO edges (src, dst, rel, first_seen, last_seen) VALUES (?,?,?,?,?)",
                            (s, d, rel, _now(), _now()))
    finally:
        if own:
            con.commit()
            con.close()


# --------------------------------------------------------------- encoding --
_PERSONISH = re.compile(r"\b(?:to|for|with|from|call|message|text|tell|ask)\s+"
                        r"([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)")

# ⚠ Capitalisation after an addressing verb is a WEAK signal, and a wrong node
#   is worse than a missing one: it links to whatever else the turn touched and
#   then drags that into recall forever. Backfilling 1018 real rows produced
#   "Good Morning (person) and WhatsApp Beta go together" from "message Good
#   Morning to ...". These are the words that look like names and are not.
_NOT_A_PERSON = {
    "good morning", "good afternoon", "good evening", "good night", "good luck",
    "happy birthday", "thank you", "thanks", "hello", "hey there", "the", "this",
    "that", "please", "sorry", "okay", "yes", "yesterday", "today", "tomorrow",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "whatsapp", "chrome", "notepad",
    "explorer", "windows", "google", "youtube", "gmail", "telegram", "vajren",
}
_HOSTISH = re.compile(r"https?://(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", re.I)


def things_in(tool: str, args: dict, request: str = "") -> list[tuple[str, str]]:
    """
    The (kind, display) pairs this step touched.

    ⚠ ARGS AND THE SPOKEN REQUEST ONLY — never a tool's result. See the module
      docstring: results are whatever the world said back, and a page that can
      name the nodes in someone's memory can steer everything that follows.
    """
    out: list[tuple[str, str]] = []
    a = args or {}

    if a.get("app"):
        out.append(("app", Path(str(a["app"])).name))
    if a.get("window"):
        out.append(("window", str(a["window"])))
    if a.get("title"):
        out.append(("window", str(a["title"])))
    for key in ("path", "file", "src", "dst", "directory"):
        v = str(a.get(key) or "")
        if v:
            p = Path(v)
            out.append(("file", p.name))
            if str(p.parent) not in (".", ""):
                out.append(("folder", str(p.parent)))
    if a.get("url"):
        m = _HOSTISH.search(str(a["url"]))
        if m:
            out.append(("host", m.group(1).lower()))
    # ⚠ `label` is deliberately NOT a node, though it is an argument.
    #   It is Vajren's own argument, but its VALUE was copied verbatim out of
    #   the app's control tree, which app_find itself returns marked untrusted.
    #   Backfilling made a node called
    #     "1 unread message Sakshi Malhotra (HCL) 2:18 AM Sone se phele"
    #   — the text of somebody's message, filed in his long-term memory. That is
    #   a privacy leak and an injection surface in one, and the signal is
    #   redundant: the person came from what he SAID and the window from the
    #   arguments, so the same link forms without the message content. The
    #   label still does its real job at the gate (core/policy.risky_word_in and
    #   _refuse_if_riskier); it just never becomes something Vajren remembers.

    # People, from what HE said. Capitalised names after an addressing verb —
    # "message Sakshi", "call Mudit India". His words, not a page's.
    for m in _PERSONISH.finditer(request or ""):
        cand = m.group(1).strip()
        if cand.lower() not in _NOT_A_PERSON and cand.split()[0].lower() not in _NOT_A_PERSON:
            out.append(("person", cand))

    seen, uniq = set(), []
    for kind, disp in out:
        k = (kind, disp.lower())
        if k not in seen and len(disp.strip()) >= 2:
            seen.add(k)
            uniq.append((kind, disp.strip()))
    return uniq


def observe(tool: str, args: dict, request: str = "", turn_id: int | None = None,
            episode_id: int | None = None, verified: bool | None = None,
            provenance: str = "observed") -> dict:
    """
    Record one step: the things it touched, and that they belong together.

    Called after the tool has run, so `verified` is known. An unverified step
    is still recorded — that Vajren TRIED is a real fact about the world — but
    it does not strengthen links, because a click that did not land is not
    evidence that two things go together.
    """
    if provenance == READ_ONLY_PROV:
        provenance = READ_ONLY_PROV                     # kept, never asserted
    pairs = things_in(tool, args, request)
    if not pairs:
        return {"entities": 0, "links": 0}
    con = _con()
    try:
        ids = []
        for kind, disp in pairs:
            eid = touch(kind, disp, con)
            if eid:
                ids.append(eid)
                con.execute(
                    "INSERT INTO observations (at, turn_id, episode_id, entity_id, tool, role, "
                    "provenance, verified, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                    (_now(), turn_id, episode_id, eid, tool, "object",
                     provenance, 1 if verified else 0, str(args.get("label") or "")[:120]))
        links = 0
        if verified is not False and provenance in BELIEVABLE:
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    link(a, b, "with", con)
                    links += 1
        con.commit()
        return {"entities": len(ids), "links": links}
    finally:
        con.close()


# ----------------------------------------------------------------- recall --
def _seeds(query: str, con: sqlite3.Connection) -> list[tuple[int, float]]:
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-_]{1,}", (query or "").lower())
             if w not in _STOP and len(w) > 2][:12]
    if not words:
        return []
    hits: dict[int, float] = {}
    for w in words:
        for r in con.execute(
                "SELECT id, mentions FROM entities WHERE merged_into IS NULL AND "
                "(name = ? OR name LIKE ?) ORDER BY mentions DESC LIMIT 4", (w, f"%{w}%")):
            # An exact name is worth more than a substring, and a thing met
            # often is worth more than a thing met once — but only mildly, or
            # the busiest node would win every question.
            exact = 1.0 if r["id"] and w == (con.execute(
                "SELECT name FROM entities WHERE id=?", (r["id"],)).fetchone()["name"]) else 0.6
            hits[r["id"]] = max(hits.get(r["id"], 0.0), exact)
    return sorted(hits.items(), key=lambda kv: -kv[1])[:MAX_SEEDS]


def activate(query: str, hops: int = HOPS, n: int = MAX_NODES) -> list[dict]:
    """
    The neighbourhood this request lights up, strongest first.

    Spreading activation: each seed passes a fraction of its charge to its
    neighbours, and they to theirs. Two hops is deliberate — three reaches
    everything on a graph this dense, which is the same as reaching nothing.
    """
    con = _con()
    try:
        charge: dict[int, float] = {}
        for eid, w in _seeds(query, con):
            charge[eid] = charge.get(eid, 0.0) + w
        if not charge:
            return []
        frontier = dict(charge)
        for _ in range(max(0, hops)):
            nxt: dict[int, float] = {}
            for eid, c in frontier.items():
                if c < 0.05:
                    continue
                for r in con.execute(
                        "SELECT e.dst, e.weight, e.rel FROM edges e JOIN entities t ON t.id = e.dst "
                        "WHERE e.src = ? AND t.merged_into IS NULL "
                        "ORDER BY e.weight DESC LIMIT 6", (eid,)):
                    add = c * DECAY_PER_HOP * min(r["weight"] / 4.0, 1.0)
                    if add >= 0.05:
                        nxt[r["dst"]] = nxt.get(r["dst"], 0.0) + add
            for eid, c in nxt.items():
                charge[eid] = charge.get(eid, 0.0) + c
            frontier = nxt
            if not frontier:
                break
        top = sorted(charge.items(), key=lambda kv: -kv[1])[:n]
        out = []
        for eid, c in top:
            r = con.execute("SELECT kind, display, mentions FROM entities WHERE id=?", (eid,)).fetchone()
            if r:
                out.append({"id": eid, "kind": r["kind"], "name": r["display"],
                            "mentions": r["mentions"], "charge": round(c, 3)})
        return out
    finally:
        con.close()


def context_for(query: str, n: int = 8) -> str:
    """
    One short block for the planner: what this request is connected to.

    Only ever built from believable rows — see the provenance ladder. Empty
    string when nothing lights up, so a cold brain costs no tokens.
    """
    lit = activate(query, n=n)
    if not lit:
        return ""
    by_kind: dict[str, list[str]] = {}
    for e in lit:
        by_kind.setdefault(e["kind"], []).append(e["name"])
    parts = [f"{k}: {', '.join(v[:5])}" for k, v in by_kind.items()]
    return "Connected to this request — " + " · ".join(parts)


# ------------------------------------------------------------- believing --
def remember_read(source: str, claim: str, turn_id: int | None = None) -> dict:
    """
    Record that a SOURCE said something. This is not Vajren believing it.

    ⚠ The only way `read` provenance enters memory, and it deliberately cannot
      write a believable row: the subject is the source, the text is attributed,
      and core.memory.recall() filters source='read' out of what the planner
      sees. Ask "what did that page say" and it comes back, attributed. Nothing
      else surfaces it, so a sentence in a file cannot become an instruction
      with a long fuse.
    """
    from core import memory
    claim = re.sub(r"\s+", " ", str(claim or "")).strip()[:400]
    src = re.sub(r"\s+", " ", str(source or "unknown")).strip()[:120]
    if len(claim) < 4:
        return {"error": "nothing to record"}
    out = memory.remember(f"{src} said: {claim}", subject="sources", source=READ_ONLY_PROV)
    eid = touch("topic", src)
    if eid:
        con = _con()
        try:
            con.execute(
                "INSERT INTO observations (at, turn_id, entity_id, tool, role, provenance, "
                "verified, detail) VALUES (?,?,?,?,?,?,?,?)",
                (_now(), turn_id, eid, "read", "context", READ_ONLY_PROV, 0, claim[:120]))
            con.commit()
        finally:
            con.close()
    return out | {"asserted": False, "provenance": READ_ONLY_PROV}


# ---------------------------------------------------------- consolidation --
def _merge_duplicates(con: sqlite3.Connection) -> int:
    """
    'whatsapp beta' and 'WhatsApp Beta' are one thing met twice.

    Merges by (kind, normalised name): punctuation and spacing dropped. The
    loser keeps its row and points at the winner via merged_into, so old
    observations still resolve instead of dangling.
    """
    merged = 0
    rows = con.execute("SELECT id, kind, name, mentions FROM entities WHERE merged_into IS NULL "
                       "ORDER BY mentions DESC, id ASC").fetchall()
    canon: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["kind"], re.sub(r"[^a-z0-9]+", "", r["name"]))
        if not key[1]:
            continue
        if key in canon and canon[key] != r["id"]:
            win = canon[key]
            con.execute("UPDATE observations SET entity_id=? WHERE entity_id=?", (win, r["id"]))
            for col in ("src", "dst"):
                con.execute(f"UPDATE OR IGNORE edges SET {col}=? WHERE {col}=?", (win, r["id"]))
            con.execute("DELETE FROM edges WHERE src = dst")
            con.execute("UPDATE entities SET mentions = mentions + ?, merged_into = ? WHERE id = ?",
                        (0, win, r["id"]))
            con.execute("UPDATE entities SET mentions = mentions + ? WHERE id = ?",
                        (r["mentions"], win))
            merged += 1
        else:
            canon[key] = r["id"]

    # ⚠ Then the misheard ones. Speech-to-text does not fail randomly: it fails
    #   the SAME way on the same name, so one person arrives as five nodes —
    #   measured here on real history: "Sakshi Malhotra" (26 mentions) plus
    #   "Sakshi Madhotra" (5), "Sakshi Malatron" (3), "Sakshima Lutron" (3),
    #   "Saksimalotra" (5). Left alone, each keeps its own links and his actual
    #   contact looks like five acquaintances he barely speaks to.
    #
    #   Conservative on purpose, because merging two real people is much worse
    #   than leaving a variant behind: people only, close spelling, AND the
    #   rare one must be much rarer than the common one. Two names that are
    #   both well attested are two people, however similar they look —
    #   "Archana" and "Aradhana" both at 13 mentions stay separate.
    from difflib import SequenceMatcher
    people = con.execute("SELECT id, name, display, mentions FROM entities "
                         "WHERE kind='person' AND merged_into IS NULL "
                         "ORDER BY mentions DESC").fetchall()
    absorbed: set[int] = set()
    for i, big in enumerate(people):
        if big["id"] in absorbed:
            continue
        for small in people[i + 1:]:
            if small["id"] in absorbed or small["mentions"] * 3 > big["mentions"]:
                continue
            a, b = big["name"].replace(" ", ""), small["name"].replace(" ", "")
            if SequenceMatcher(None, a, b).ratio() < 0.82:
                continue
            con.execute("UPDATE observations SET entity_id=? WHERE entity_id=?", (big["id"], small["id"]))
            for col in ("src", "dst"):
                con.execute(f"UPDATE OR IGNORE edges SET {col}=? WHERE {col}=?", (big["id"], small["id"]))
            con.execute("DELETE FROM edges WHERE src = dst")
            con.execute("UPDATE entities SET merged_into=? WHERE id=?", (big["id"], small["id"]))
            con.execute("UPDATE entities SET mentions = mentions + ? WHERE id = ?",
                        (small["mentions"], big["id"]))
            absorbed.add(small["id"])
            merged += 1
    return merged


def _promote(con: sqlite3.Connection) -> list[str]:
    """
    What has happened often enough to be worth saying out loud.

    A pair of things seen together in PROMOTE_AFTER separate verified steps is
    no longer a coincidence; it is how Mudit works. That becomes an `inferred`
    fact, which the planner may use. Anything whose evidence came from a `read`
    row cannot get here — the join filters on believable provenance.
    """
    # ⚠ Writes through `con`, NOT through memory.remember(). remember() opens
    #   its own connection, and calling it here — inside consolidate()'s open
    #   write transaction — deadlocks on the WAL write lock ("database is
    #   locked") the moment there is anything to promote. Found on the first
    #   backfill of 1018 real audit rows. One pass, one connection.
    made = []
    rows = con.execute(
        "SELECT e.src, e.dst, e.evidence, a.kind ka, a.display da, b.kind kb, b.display db "
        "FROM edges e JOIN entities a ON a.id = e.src JOIN entities b ON b.id = e.dst "
        "WHERE e.src < e.dst AND e.evidence >= ? AND a.merged_into IS NULL "
        "AND b.merged_into IS NULL ORDER BY e.evidence DESC LIMIT 12",
        (PROMOTE_AFTER,)).fetchall()
    for r in rows:
        believable = con.execute(
            "SELECT COUNT(*) c FROM observations WHERE entity_id IN (?,?) AND provenance IN "
            "(%s) AND verified = 1" % ",".join("?" * len(BELIEVABLE)),
            (r["src"], r["dst"], *BELIEVABLE)).fetchone()["c"]
        if believable < PROMOTE_AFTER:
            continue
        line = f"{r['da']} ({r['ka']}) and {r['db']} ({r['kb']}) go together"
        known = con.execute("SELECT 1 FROM facts WHERE subject='patterns' AND fact=? "
                            "AND valid_to IS NULL", (line,)).fetchone()
        if not known:
            con.execute("INSERT OR IGNORE INTO facts (subject, fact, source) VALUES (?,?,?)",
                        ("patterns", line, "inferred"))
            made.append(line)
    return made


def _decay(con: sqlite3.Connection) -> dict:
    """
    Forgetting, which is a feature. An edge not reinforced in 30 days loses a
    fifth of its weight; below EDGE_FLOOR it goes. Without this the graph only
    ever grows and every recall drifts toward whatever was busy in month one.
    """
    con.execute("UPDATE edges SET weight = weight * 0.8 "
                "WHERE last_seen < datetime('now', '-30 days')")
    gone = con.execute("DELETE FROM edges WHERE weight < ?", (EDGE_FLOOR,)).rowcount
    orphan = con.execute(
        "DELETE FROM entities WHERE merged_into IS NULL AND mentions <= 1 "
        "AND last_seen < datetime('now', '-60 days') "
        "AND id NOT IN (SELECT entity_id FROM observations)").rowcount
    return {"edges_forgotten": gone, "entities_forgotten": orphan}


def _dedupe_lessons(con: sqlite3.Connection, keep: int = 40) -> int:
    """
    The lessons pile, capped. Every bad turn files a line; nothing ever removed
    one, so the same lesson accumulated in a dozen near-identical forms and the
    planner saw the same advice three times per request. Keep the newest of any
    duplicated (fault, fix), then cap the whole subject at `keep`.
    """
    rows = con.execute("SELECT id, fact FROM facts WHERE subject='lessons' AND valid_to IS NULL "
                       "ORDER BY id DESC").fetchall()
    seen, dupes = set(), []
    for r in rows:
        tail = r["fact"].split(": ", 1)[-1]
        key = re.sub(r"[^a-z0-9 ]+", "", tail.lower())[:90]
        if key in seen:
            dupes.append(r["id"])
        else:
            seen.add(key)
    live = [r["id"] for r in rows if r["id"] not in dupes]
    dupes += live[keep:]
    if dupes:
        con.execute(f"UPDATE facts SET valid_to = datetime('now') WHERE id IN "
                    f"({','.join('?' * len(dupes))})", dupes)
    return len(dupes)


def consolidate() -> dict:
    """
    The sleep pass. Cheap, deterministic, no model — run it whenever idle.

    Merge what is the same thing, promote what has proved itself, forget what
    stopped mattering, and cap the lessons. This is the whole of "improves
    itself automatically": no model writes anything here, so nothing it does
    can be talked into existence by a page.
    """
    con = _con()
    try:
        merged = _merge_duplicates(con)
        promoted = _promote(con)
        lessons = _dedupe_lessons(con)
        decayed = _decay(con)
        con.commit()
        return {"merged": merged, "promoted": promoted, "lessons_retired": lessons, **decayed}
    finally:
        con.close()


_last_consolidated = 0.0
CONSOLIDATE_EVERY = 600.0          # seconds


def consolidate_later(force: bool = False) -> bool:
    """
    Fire and forget, off the hot path — but NOT after every single turn.

    ⚠ THROTTLED, and this was measured the hard way. The first version ran the
      whole pass after every completed turn. On the real database — 125 things,
      1,124 observations — that is a full scan plus per-row updates holding
      SQLite's single write lock, and the NEXT turn's record_turn/observe then
      waited on it. The face suite went red with a timeout waiting for `heard`,
      which is the person waiting for Vajren to answer.

      It is also just wrong as a model of the thing: a brain does not
      consolidate after every sentence, it does it at rest. Once every ten
      minutes at most, and only after a turn has finished, is both cheaper and
      closer to the idea. Returns whether it actually started one.
    """
    global _last_consolidated
    import threading
    import time as _time
    if not force and _time.monotonic() - _last_consolidated < CONSOLIDATE_EVERY:
        return False
    _last_consolidated = _time.monotonic()

    def go():
        try:
            consolidate()
        except Exception:                                          # noqa: BLE001
            pass
    threading.Thread(target=go, daemon=True, name="vajren-consolidate").start()
    return True


# ----------------------------------------------------------------- report --
def stats() -> dict:
    con = _con()
    try:
        e = con.execute("SELECT COUNT(*) FROM entities WHERE merged_into IS NULL").fetchone()[0]
        g = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        o = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        r = con.execute("SELECT COUNT(*) FROM observations WHERE provenance='read'").fetchone()[0]
        return {"entities": e, "links": g // 2, "observations": o, "read_only_rows": r}
    finally:
        con.close()


def report(limit: int = 25) -> dict:
    """What it knows, and why it thinks so. The correction surface."""
    con = _con()
    try:
        top = [dict(r) for r in con.execute(
            "SELECT kind, display, mentions, last_seen FROM entities WHERE merged_into IS NULL "
            "ORDER BY mentions DESC, last_seen DESC LIMIT ?", (limit,))]
        strong = [dict(r) for r in con.execute(
            "SELECT a.display src, b.display dst, e.weight, e.evidence FROM edges e "
            "JOIN entities a ON a.id=e.src JOIN entities b ON b.id=e.dst "
            "WHERE e.src < e.dst ORDER BY e.weight DESC LIMIT ?", (limit,))]
        return {"stats": stats(), "things": top, "links": strong}
    finally:
        con.close()


def forget_entity(name: str) -> dict:
    """
    'You are wrong about that.' Removes a thing and everything attached to it.

    The correction path matters more than it looks: a memory that cannot be
    corrected is one Mudit has to work around forever, and he will stop
    trusting the parts that ARE right.
    """
    con = _con()
    try:
        rows = con.execute("SELECT id, display FROM entities WHERE name LIKE ?",
                           (f"%{name.strip().lower()}%",)).fetchall()
        if not rows:
            return {"forgot": [], "count": 0}
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM observations WHERE entity_id IN ({marks})", ids)
        con.execute(f"DELETE FROM edges WHERE src IN ({marks}) OR dst IN ({marks})", ids + ids)
        con.execute(f"DELETE FROM entities WHERE id IN ({marks})", ids)
        con.commit()
        return {"forgot": [r["display"] for r in rows], "count": len(ids)}
    finally:
        con.close()


def backfill(limit: int = 5000) -> dict:
    """
    Build the graph from history that was already recorded.

    ⚠ Every tool call Vajren has ever made is in `audit` — tool, arguments,
      and whether the post-condition passed — and every request is in
      `episodes`. So the brain does not have to start empty and learn Mudit
      from scratch; it can read what it already did. Idempotent: entity upserts
      and edge strengthening are both by-key, so running it twice adds mentions
      but no duplicates, and consolidate() flattens those anyway.

      Arguments only, again. `result_json` is in the same rows and is NOT read:
      a page title that came back from a click is the world talking, and the
      whole provenance rule exists to keep the world from naming the things in
      his memory. The rule does not get relaxed for old data.
    """
    import json
    con = _con()
    try:
        rows = con.execute(
            "SELECT a.tool, a.args_json, a.verified, a.episode_id, e.request "
            "FROM audit a LEFT JOIN episodes e ON e.id = a.episode_id "
            "ORDER BY a.id ASC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    seen = links = 0
    for r in rows:
        try:
            args = json.loads(r["args_json"] or "{}")
        except (ValueError, TypeError):
            continue
        args.pop("_key", None)
        out = observe(r["tool"], args, request=r["request"] or "",
                      episode_id=r["episode_id"],
                      verified=bool(r["verified"]), provenance="observed")
        seen += out["entities"]
        links += out["links"]
    return {"rows": len(rows), "entities_touched": seen, "links_made": links}
