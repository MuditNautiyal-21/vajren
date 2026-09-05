"""33 - Where do the seconds go? One real multi-step request, per plan call.

Prints, for every planner call: wall time, tokens the model had to READ
(after the KV cache) and how long, tokens it WROTE and how long, and the
prompt size. This is the measurement behind any latency work — see J-049
before trusting an intuition about it.

    .venv\\Scripts\\python.exe -X utf8 scripts\\33-plan-cost.py
"""
from __future__ import annotations

import os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VAJREN_DB", os.path.join(tempfile.gettempdir(), f"vajren-perf-{os.getpid()}.db"))

from langgraph.types import Command                 # noqa: E402
from core.graph import build                        # noqa: E402
from core.tools.apps import close_window            # noqa: E402

REQ = ("Write the word perf into C:\\vajren\\sandbox\\perf-a.txt, then write the word check "
       "into C:\\vajren\\sandbox\\perf-b.txt, then read perf-a.txt back and tell me what it says.")

app = build()
cfg = {"configurable": {"thread_id": f"perf-{int(time.time())}"}}
t = time.perf_counter()
st = app.invoke({"request": REQ, "sources": set(), "session_id": "perf"}, cfg)
gates = 0
while "__interrupt__" in st and gates < 6:
    gates += 1
    st = app.invoke(Command(resume="approve"), cfg)
total = time.perf_counter() - t
tools = [h["tool"] for h in st.get("history", [])]
print(f"\nTOTAL {total:.1f}s   gates={gates}   tools={tools}")
print(f"{'plan':>5} {'wall':>6} {'read tok':>9} {'read ms':>8} {'wrote':>6} {'wrote ms':>9} {'prompt':>7} {'cached':>7} {'chars':>7}")
sum_read = sum_wrote = 0.0
for i, tm in enumerate(st.get("timings", []), 1):
    print(f"{i:>5} {tm.get('wall_s', 0):>6} {tm.get('read_n') or 0:>9} {int(tm.get('read_ms') or 0):>8} "
          f"{tm.get('wrote_n') or 0:>6} {int(tm.get('wrote_ms') or 0):>9} {tm.get('prompt_tokens') or 0:>7} "
          f"{tm.get('cached') or 0:>7} {tm.get('chars_in') or 0:>7}")
    sum_read += (tm.get("read_ms") or 0) / 1000; sum_wrote += (tm.get("wrote_ms") or 0) / 1000
plans = len(st.get("timings", []))
print(f"\n  {plans} plan calls: reading {sum_read:.1f}s, writing {sum_wrote:.1f}s, "
      f"other (tools, graph, gate) {total - sum_read - sum_wrote:.1f}s")
close_window("perf-", force=True)
for f in ("perf-a.txt", "perf-b.txt"):
    (ROOT / "sandbox" / f).unlink(missing_ok=True)
