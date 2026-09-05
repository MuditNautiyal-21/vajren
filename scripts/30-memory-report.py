"""30 - What Vajren knows, and what it has stopped asking about.

    .venv\\Scripts\\python.exe -X utf8 scripts\\30-memory-report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import memory                                  # noqa: E402

st = memory.stats()
print(f"\nmemory/vajren.db  {st['db_mb']} MB   turns={st['turns']}  facts={st['facts']}  trusted shapes={st['trusted_shapes']}")

print("\n== facts it holds (newest last)")
for f in memory.all_facts():
    print(f"  [{f['source']:8s}] {f['fact']}    ({f['subject']}, {f['valid_from'][:10]})")

print("\n== shapes it no longer asks about")
rows = memory.trust_report()
granted = [r for r in rows if r["granted_at"] and not r["revoked_at"]]
for r in granted:
    print(f"  {r['tool']:14s} {r['pattern']!r:44s} approvals={r['approvals']}  since {r['granted_at'][:16]}")
if not granted:
    print("  (none — everything still asks)")

print("\n== shapes still earning (consecutive approvals so far)")
for r in rows:
    if not r["granted_at"]:
        print(f"  {r['tool']:14s} {r['pattern']!r:44s} {r['approvals']}/{memory.TRUST_AFTER}   cancels={r['cancels']}")

print("\n== the last few turns")
for t in memory.recent_turns(8):
    print(f"  [{t['at'][:16]}] {t['status']:9s} {t['request'][:70]!r} -> {t['outcome'][:60]!r}")
print()
