"""
Tool registry.

Every tool obeys four rules, no exceptions:
  1. It declares a Pydantic args schema (so calls are grammar-constrained, and
     malformed calls are rejected here, before anything runs).
  2. It is idempotent — the same idempotency key must never act twice. Enforced
     here against the audit table, not left to the tool.
  3. If it mutates anything, it returns an `undo_ref` (snapshot path, trash path,
     git sha, draft id) that `undo()` can act on.
  4. It has a post-condition in core/verify.py.

A tool that breaks any of those four does not get registered.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "memory" / "vajren.db"
SCHEMA = ROOT / "memory" / "schema.sql"

REGISTRY: dict[str, Callable[..., dict]] = {}
SCHEMAS: dict[str, Type[BaseModel]] = {}


def tool(name: str, schema: Type[BaseModel], *, mutating: bool = False):
    """Register a tool. The schema is mandatory — there is no untyped tool."""
    def wrap(fn):
        fn._vajren_mutating = mutating
        REGISTRY[name] = fn
        SCHEMAS[name] = schema
        return fn
    return wrap


def catalog() -> str:
    """The tool list as the planner sees it: name, purpose, JSON schema."""
    lines = []
    for name, schema in SCHEMAS.items():
        doc = (REGISTRY[name].__doc__ or "").strip().splitlines()[0]
        props = schema.model_json_schema().get("properties", {})
        args = ", ".join(
            f"{k}: {v.get('type', 'any')}{'' if k in schema.model_json_schema().get('required', []) else '?'}"
            for k, v in props.items()
        )
        lines.append(f"- {name}({args}) — {doc}")
    return "\n".join(lines)


# ------------------------------------------------------------------- audit --
def _con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    # Apply the schema on first contact so the audit table always exists. The
    # schema is all IF NOT EXISTS, so this is safe to run every time.
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def _audit(episode_id: int | None, action: dict, result: dict, key: str) -> None:
    with _con() as con:
        con.execute(
            "INSERT INTO audit (episode_id, tool, args_json, tier, result_json, verified, undo_ref)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                episode_id,
                action["tool"],
                json.dumps({"_key": key, **action.get("args", {})}, default=str),
                action.get("_tier", "unknown"),
                json.dumps(result, default=str)[:20000],
                None,
                result.get("undo_ref"),
            ),
        )


def _prior(key: str, episode_id: int | None) -> dict | None:
    """
    A previous successful execution of this exact call WITHIN THIS EPISODE.

    ⚠ Scoped to the episode on purpose. Idempotency here exists to stop a
    crash-and-resume from sending the same email twice inside one task. It must
    NOT mean "you asked for this last Tuesday, so I won't do it again": the
    first version was global and unbounded, so a write → trash → write-the-same
    -thing-again sequence silently did nothing the second time and reported
    success. A replay that skips work the user currently wants is the same
    class of lie as a tool that claims it acted and didn't.

    No episode means no crash-recovery context, so no replay.
    """
    if episode_id is None:
        return None
    with _con() as con:
        row = con.execute(
            "SELECT result_json FROM audit WHERE episode_id = ? AND args_json LIKE ?"
            " ORDER BY id DESC LIMIT 1",
            (episode_id, f'{{"_key": "{key}"%'),
        ).fetchone()
    if not row:
        return None
    result = json.loads(row[0])
    return None if result.get("error") else result


def new_episode(request: str, channel: str = "script") -> int:
    """
    Open an episode and return its id. Pass it to run_tool.

    ⚠ Not optional bookkeeping. `audit.episode_id` is a FOREIGN KEY, so an id
    with no episodes row makes the audit INSERT fail — and that failure was
    caught and turned into a field on the result, meaning the tool ran, the
    audit log did not record it, and nothing said so. An audit trail that drops
    rows quietly is worse than none, because you would trust it.
    """
    with _con() as con:
        cur = con.execute("INSERT INTO episodes (channel, request) VALUES (?,?)",
                          (channel, request))
        return int(cur.lastrowid)


def mark_verified(key: str | None, episode_id: int | None, ok: bool) -> None:
    """Close the audit row for `key` with the post-condition's verdict."""
    if not key:
        return
    with _con() as con:
        con.execute(
            "UPDATE audit SET verified = ? WHERE id = (SELECT MAX(id) FROM audit"
            " WHERE args_json LIKE ? AND (episode_id IS ? OR episode_id = ?))",
            (1 if ok else 0, f'{{"_key": "{key}"%', episode_id, episode_id),
        )


def last_undoable() -> tuple[str, str, str] | None:
    """
    The most recent file change that can still be reversed, as
    (tool, undo_ref, original_path) — or None.

    Skips undo_file itself: undoing an undo is legal at the tool level but as a
    REPL command it is a trap, because the second /undo silently means the
    opposite of the first.
    """
    with _con() as con:
        rows = con.execute(
            "SELECT tool, undo_ref FROM audit"
            " WHERE undo_ref IS NOT NULL AND undo_ref != '' AND tool != 'undo_file'"
            " ORDER BY id DESC LIMIT 20"
        ).fetchall()
    for tool, ref in rows:
        parts = ref.split("|", 2)
        if len(parts) == 3 and parts[0] in ("snapshot", "trash", "absent"):
            return tool, ref, parts[2]
    return None


def close_episode(episode_id: int, outcome: str, error: str | None = None) -> None:
    with _con() as con:
        con.execute("UPDATE episodes SET ended_at = datetime('now'), outcome = ?, error = ?"
                    " WHERE id = ?", (outcome, error, episode_id))


def idempotency_key(name: str, args: dict) -> str:
    # sha256, NOT hash(): Python salts str hashes per process, so hash() gives a
    # different key after every restart — which is exactly when you need it.
    blob = json.dumps(args, sort_keys=True, default=str)
    return f"{name}:{hashlib.sha256(blob.encode()).hexdigest()[:24]}"


# --------------------------------------------------------------------- run --
def run_tool(action: dict, *, episode_id: int | None = None) -> dict:
    name = action["tool"]
    fn = REGISTRY.get(name)
    if fn is None:
        return {"error": f"no such tool: {name}"}

    # Rule 1: validate against the schema BEFORE anything runs.
    try:
        args = SCHEMAS[name](**action.get("args", {})).model_dump()
    except ValidationError as e:
        return {"error": f"bad arguments for {name}: {e.errors()[0]['msg']}", "mutating": False}

    key = action.get("idempotency_key") or idempotency_key(name, args)

    # Rule 2: a mutating call that already succeeded under this key does not
    # run again. It returns what it returned the first time.
    if getattr(fn, "_vajren_mutating", False):
        prior = _prior(key, episode_id)
        if prior is not None:
            prior["replayed"] = True
            return prior

    started = time.time()
    try:
        result = fn(**args)
    except Exception as e:  # a tool exception is data, not a crash
        result = {"error": f"{type(e).__name__}: {e}"}

    result.setdefault("mutating", getattr(fn, "_vajren_mutating", False))
    result["idempotency_key"] = key
    result["elapsed_s"] = round(time.time() - started, 3)

    try:
        _audit(episode_id, {**action, "args": args}, result, key)
    except Exception as e:
        result["audit_error"] = f"{type(e).__name__}: {e}"  # loud, never fatal
    return result


# Registration happens on import. Add modules here as they are built.
from core.tools import apps, files, shell  # noqa: E402,F401
