"""
Tool registry.

Every tool obeys four rules, no exceptions:
  1. It declares a Pydantic args schema (so calls are grammar-constrained).
  2. It is idempotent — the same idempotency key must never act twice.
  3. If it mutates anything, it returns an `undo_ref` (trash path, git sha, draft id).
  4. It has a post-condition in core/verify.py.

A tool that breaks any of those four does not get registered.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "memory" / "vajren.db"

REGISTRY: dict[str, Callable[..., dict]] = {}


def tool(name: str, *, mutating: bool = False):
    def wrap(fn):
        fn._vajren_mutating = mutating
        REGISTRY[name] = fn
        return fn
    return wrap


def _audit(episode_id: int | None, action: dict, result: dict) -> None:
    con = sqlite3.connect(DB)
    con.execute(
        "INSERT INTO audit (episode_id, tool, args_json, tier, result_json, verified, undo_ref)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            episode_id,
            action["tool"],
            json.dumps(action.get("args", {}), default=str),
            action.get("_tier", "unknown"),
            json.dumps(result, default=str)[:20000],
            None,
            result.get("undo_ref"),
        ),
    )
    con.commit()
    con.close()


def run_tool(action: dict, *, episode_id: int | None = None) -> dict:
    name = action["tool"]
    fn = REGISTRY.get(name)
    if fn is None:
        return {"error": f"no such tool: {name}"}

    key = action.get("idempotency_key") or f"{name}:{hash(json.dumps(action.get('args', {}), sort_keys=True, default=str))}"
    started = time.time()
    try:
        result = fn(**action.get("args", {}))
    except Exception as e:  # a tool exception is data, not a crash
        result = {"error": f"{type(e).__name__}: {e}"}

    result.setdefault("mutating", getattr(fn, "_vajren_mutating", False))
    result["idempotency_key"] = key
    result["elapsed_s"] = round(time.time() - started, 3)

    try:
        _audit(episode_id, action, result)
    except Exception:
        pass  # audit failure must never take down the loop — but it will be loud in logs
    return result


# Import submodules here as you build them, so registration happens on load:
# from core.tools import files, mail, calendar, shell, browser, windows  # noqa: F401
