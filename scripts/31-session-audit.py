"""31 - How logical and how efficient was it? Scored from the session log.

Mudit: "monitor its latest actions and the commands given against how it
acted; gauge how logical and how efficient." This makes that a number, per
turn and per session, from logs/voice-sessions/*.jsonl — so the question can
be asked after every session and the answer compared to last time. That is
the evolution loop: measure, fix, measure.

Per turn it counts:
  steps      tool calls that ran and verified
  wasted     proposals blocked as repeats, failed steps, or steps re-planned
  asks       spoken approvals it needed
  cancelled  Mudit said no
  promise    it declared done with a plan instead of a result
  time       seconds from request to answer
and prints the worst things it did, in plain words.

    .venv\\Scripts\\python.exe -X utf8 scripts\\31-session-audit.py [N sessions]
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESS = ROOT / "logs" / "voice-sessions"
PROMISE = re.compile(r"^\W*(?:okay|ok|sure|now)?\W*(i'?ll\b|i will\b|let me\b|i'?m going to\b|"
                     r"(?:open|start|launch|creat|writ|search|navigat|bring|clos)\w*ing\b)", re.I)


def turns_of(path: Path) -> list[dict]:
    turns, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t == "request":
            cur = {"request": e["text"], "steps": [], "asks": 0, "cancel": False,
                   "summary": "", "elapsed": None, "wasted": [], "repeats": 0}
            turns.append(cur)
        elif cur is None:
            continue
        elif t == "step":
            cur["steps"].append(e)
            err = str(e.get("error") or "")
            if "already did" in err:
                cur["repeats"] += 1
                cur["wasted"].append(f"repeated {e['tool']}")
            elif not e.get("verified"):
                cur["wasted"].append(f"{e['tool']} failed: {err[:60] or 'unverified'}")
        elif t == "gate":
            cur["asks"] += 1
        elif t == "verdict" and e.get("verdict") == "cancel":
            cur["cancel"] = True
        elif t == "done":
            cur["summary"] = e.get("summary", "")
            cur["elapsed"] = e.get("elapsed")
    return turns


def score(turn: dict) -> tuple[int, list[str]]:
    """0-100 and the reasons points were lost."""
    good = [s for s in turn["steps"] if s.get("verified")]
    notes, pts = [], 100
    # Same tool twice with verified=True and identical args is the classic
    # "opened it again" even when the guard did not catch it.
    seen = set()
    for s in good:
        key = (s["tool"], json.dumps(s.get("args"), sort_keys=True))
        if key in seen:
            notes.append(f"did {s['tool']} twice with the same arguments"); pts -= 25
        seen.add(key)
    opens = [s for s in good if s["tool"] in ("open_url", "browser_open", "open_app")]
    hosts = [re.sub(r"^https?://(www\.)?", "", str(s["args"].get("url", ""))).split("/")[0]
             for s in opens if s["args"].get("url")]
    if len(hosts) != len(set(hosts)):
        notes.append("opened the same site more than once (two browsers?)"); pts -= 25
    if turn["repeats"]:
        notes.append(f"proposed an already-done step {turn['repeats']}x (caught, but ~4 s each)")
        pts -= 10 * turn["repeats"]
    fails = [w for w in turn["wasted"] if "failed" in w]
    if fails:
        notes.append("; ".join(fails[:2])); pts -= 10 * len(fails)
    if turn["summary"] and PROMISE.search(turn["summary"]) and not turn["cancel"]:
        notes.append(f"ended with a promise, not a result: {turn['summary'][:60]!r}"); pts -= 30
    if turn["asks"] > 1:
        notes.append(f"asked permission {turn['asks']} times in one request"); pts -= 8 * (turn["asks"] - 1)
    if turn["cancel"]:
        notes.append("Mudit cancelled it"); pts -= 15
    if turn["elapsed"] and turn["elapsed"] > 60:
        notes.append(f"took {turn['elapsed']} s"); pts -= 10
    return max(pts, 0), notes


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    files = sorted(glob.glob(str(SESS / "*.jsonl")), key=os.path.getmtime)[-n:]
    overall = []
    for f in files:
        turns = turns_of(Path(f))
        if not turns:
            continue
        print(f"\n=== {Path(f).name}  ({len(turns)} turns)")
        for i, t in enumerate(turns, 1):
            s, notes = score(t)
            overall.append(s)
            good = [x["tool"] for x in t["steps"] if x.get("verified")]
            print(f"  turn {i}  {s:3d}/100  {t['elapsed'] or '?':>5}s  asks={t['asks']}  "
                  f"did={' '.join(good) or 'nothing'}")
            print(f"           asked: {t['request'][:100]!r}")
            print(f"           said:  {t['summary'][:100]!r}")
            for note in notes:
                print(f"           ✗ {note}")
    if overall:
        print(f"\n  sessions: {len(files)}   turns: {len(overall)}   "
              f"mean score: {sum(overall)/len(overall):.0f}/100   "
              f"turns at 100: {sum(1 for s in overall if s == 100)}/{len(overall)}")


if __name__ == "__main__":
    main()
