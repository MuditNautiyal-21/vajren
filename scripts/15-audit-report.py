"""15 - What does Vajren say it did? Read the audit trail, not the transcript.

The transcript is what it TOLD you. This is what it recorded. They should agree;
the day they don't is the day this script earns its keep.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
con = sqlite3.connect(ROOT / "memory" / "vajren.db")

print("\n=== last 8 episodes ===")
rows = con.execute(
    "SELECT id, channel, substr(request,1,58), COALESCE(outcome,'** still open **'),"
    "       COALESCE(ended_at,'-') FROM episodes ORDER BY id DESC LIMIT 8").fetchall()
for i, ch, req, outcome, ended in rows:
    print(f"  #{i:<4} {ch:<7} {outcome:<16} {req}")

open_n = con.execute("SELECT COUNT(*) FROM episodes WHERE outcome IS NULL").fetchone()[0]
tot_n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"\n  {tot_n - open_n}/{tot_n} episodes closed with an outcome")

print("\n=== last 10 tool calls ===")
rows = con.execute(
    "SELECT id, episode_id, tool, verified, undo_ref, args_json"
    " FROM audit ORDER BY id DESC LIMIT 10").fetchall()
for i, ep, tool, ver, undo, args in rows:
    a = {k: v for k, v in json.loads(args).items() if k != "_key"}
    mark = {1: "ok", 0: "FAILED"}.get(ver, "?")
    path = a.get("path") or a.get("command") or ""
    print(f"  #{i:<4} ep={str(ep or '-'):<5} {mark:<7} {tool:<14} {str(path)[:46]}"
          f"{'  [undoable]' if undo else ''}")

unver = con.execute("SELECT COUNT(*) FROM audit WHERE verified IS NULL").fetchone()[0]
tot_a = con.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
print(f"\n  {tot_a - unver}/{tot_a} tool calls carry a verification verdict")
if unver:
    print("  (read-only calls and direct script calls have no verdict - expected)")
sys.exit(0)
