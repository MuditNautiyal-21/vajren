"""21 - Read a voice session the way Claude needs to: as numbers and a transcript.

    .venv\\Scripts\\python.exe scripts\\21-session-report.py            # latest
    .venv\\Scripts\\python.exe scripts\\21-session-report.py <stamp>    # a specific one

This is the other half of "you talk, I monitor". The face writes every event to
logs/voice-sessions/<stamp>.jsonl; this turns it into what matters:
  - what was heard, at what confidence, and what the parser decided
  - stt / think / tts latency per turn
  - anything dropped, queued, errored, or flagged as an injection attempt
"""
from __future__ import annotations

import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "logs" / "voice-sessions"

files = sorted(DIR.glob("*.jsonl"))
if not files:
    print("no sessions yet"); sys.exit(0)
path = DIR / f"{sys.argv[1]}.jsonl" if len(sys.argv) > 1 else files[-1]
ev = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
ts = lambda e: datetime.fromisoformat(e["t"])

print(f"\n== session {path.stem}   {len(ev)} events   "
      f"{(ts(ev[-1]) - ts(ev[0])).total_seconds():.0f}s\n")

heard = [e for e in ev if e["type"] == "heard"]
spoken = [e for e in heard if e.get("audio_seconds")]        # via mic, not typed
print(f"  utterances heard   {len(heard)}   ({len(spoken)} spoken, {len(heard) - len(spoken)} typed)")
if spoken:
    confs = [e["conf"] for e in spoken if e.get("text")]
    print(f"  spoken confidence  min {min(confs):.2f}  median {st.median(confs):.2f}  max {max(confs):.2f}"
          if confs else "  spoken confidence  (nothing transcribed)")
    print(f"  stt latency        median {st.median(e['stt_seconds'] for e in spoken):.1f}s")
    empties = sum(1 for e in spoken if not e.get("text"))
    if empties:
        print(f"  heard nothing      {empties}x  (mic level? gate too high?)")

verd = [e for e in ev if e["type"] == "verdict"]
if verd:
    from collections import Counter
    c = Counter(e["verdict"] for e in verd)
    print(f"  verdicts           " + ", ".join(f"{k} {v}" for k, v in c.items()))

# think latency: request -> first say/gate ; tts seconds
think = []
for i, e in enumerate(ev):
    if e["type"] == "request":
        for f in ev[i + 1:]:
            if f["type"] in ("gate", "done"):
                think.append((ts(f) - ts(e)).total_seconds()); break
if think:
    print(f"  think latency      median {st.median(think):.1f}s  max {max(think):.1f}s")
tts = [e["seconds"] for e in ev if e["type"] == "tts"]
if tts:
    print(f"  tts synth          median {st.median(tts):.1f}s  for {len(tts)} utterances")

flags = {k: sum(1 for e in ev if e["type"] == k)
         for k in ("queued", "queued_replaced", "busy_dropped", "playback_timeout", "error", "utterance_too_short")}
flags = {k: v for k, v in flags.items() if v}
if flags:
    print(f"  flags              {flags}")
inj = [e for e in ev if e["type"] == "step" and e.get("injection")]
if inj:
    print(f"  INJECTION flagged  {len(inj)}x")

print("\n== transcript")
for e in ev:
    k = e["type"]
    if k == "heard":
        tag = f"you  ({e['conf']:.2f}{'  ' + e['verdict'] if e.get('verdict') else ''})"
        print(f"  {tag:<24} {e['text'] or '(nothing)'}")
    elif k == "say":
        print(f"  {'vajren':<24} {e['text'][:140]}")
    elif k == "step":
        mark = "ok " if e["verified"] else "FAIL"
        print(f"  {'  step ' + mark:<24} {e['tool']}  {e.get('error') or ''}")
    elif k == "button":
        print(f"  {'you (button)':<24} {e['verdict']}")
    elif k == "error":
        print(f"  {'ERROR':<24} {e['error']}")
