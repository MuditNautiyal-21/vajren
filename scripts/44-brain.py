"""
What Vajren's brain currently holds, and why it thinks so.

    .\.venv\Scripts\python.exe scripts\44-brain.py              # what it knows
    .\.venv\Scripts\python.exe scripts\44-brain.py connected to sakshi
    .\.venv\Scripts\python.exe scripts\44-brain.py --consolidate # run the sleep pass now
    .\.venv\Scripts\python.exe scripts\44-brain.py --forget "old project"

⚠ THIS IS THE CORRECTION SURFACE, and it is the reason there is no Obsidian
  vault. A vault would be a second copy of the truth that drifts from the
  first — the exact failure the journal warns about for docs. What a vault
  would really have bought is the ability to SEE what Vajren believes and say
  "no, that's wrong". That is this file, without the second copy.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import brain, memory                                     # noqa: E402

args = sys.argv[1:]

if args and args[0] == "--consolidate":
    print("running the sleep pass...")
    out = brain.consolidate()
    print(f"  merged duplicates   {out['merged']}")
    print(f"  lessons retired     {out['lessons_retired']}")
    print(f"  links forgotten     {out['edges_forgotten']}")
    print(f"  things forgotten    {out['entities_forgotten']}")
    for line in out["promoted"]:
        print(f"  promoted to a fact  {line}")
    sys.exit(0)

if args and args[0] == "--backfill":
    print("reading everything it has already done...")
    out = brain.backfill()
    print(f"  audit rows read     {out['rows']}")
    print(f"  things touched      {out['entities_touched']}")
    print(f"  links made          {out['links_made']}")
    c = brain.consolidate()
    print(f"  then merged         {c['merged']} duplicates")
    for line in c["promoted"]:
        print(f"  promoted to a fact  {line}")
    sys.exit(0)

if args and args[0] == "--forget":
    if len(args) < 2:
        print("what should it forget?")
        sys.exit(1)
    out = brain.forget_entity(" ".join(args[1:]))
    print(f"forgot {out['count']}: {', '.join(out['forgot']) or 'nothing matched'}")
    sys.exit(0)

if args:
    q = " ".join(args)
    print(f"\n== what '{q}' lights up\n")
    lit = brain.activate(q)
    if not lit:
        print("  nothing — it has not met these things yet")
    for e in lit:
        bar = "#" * max(1, int(e["charge"] * 12))
        print(f"  {e['charge']:>5.2f} {bar:<14} {e['kind']:<7} {e['name']}  (met {e['mentions']}x)")
    print(f"\n  what the planner would be told:\n    {brain.context_for(q) or '(nothing)'}")
    sys.exit(0)

r = brain.report()
s = r["stats"]
m = memory.stats()
print("\n== the brain\n")
print(f"  things it has met      {s['entities']}")
print(f"  links between them     {s['links']}")
print(f"  actions recorded       {s['observations']}")
print(f"  quarantined (read)     {s['read_only_rows']}   <- recorded, never believed")
print(f"  turns / facts / trust  {m['turns']} / {m['facts']} / {m['trusted_shapes']}")
print(f"  database               {m['db_mb']} MB")

print("\n== what it has met most\n")
for t in r["things"][:15]:
    print(f"  {t['mentions']:>4}x  {t['kind']:<7} {t['display']}")

print("\n== what it has connected\n")
if not r["links"]:
    print("  nothing yet — links form when two things are handled in one turn")
for e in r["links"][:15]:
    print(f"  {e['weight']:>5.2f} ({e['evidence']:>2} times)  {e['src']}  <->  {e['dst']}")

print("\n== what it believes, and on whose word\n")
by_source: dict[str, list[str]] = {}
for f in memory.all_facts():
    if f["subject"] == "lessons":
        continue
    by_source.setdefault(f["source"] or "?", []).append(f["fact"])
for src in ("stated", "corrected", "observed", "inferred", "read"):
    rows = by_source.get(src, [])
    if not rows:
        continue
    tag = "  <- NEVER used as fact" if src == "read" else ""
    print(f"  [{src}] {len(rows)}{tag}")
    for f in rows[:6]:
        print(f"      {f[:110]}")
print("\n  correct it with:  scripts\\44-brain.py --forget \"<thing>\"")
print("  or just tell Vajren: \"forget that ...\"\n")
