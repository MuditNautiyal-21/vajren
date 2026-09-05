"""23 - Where does a turn actually go? Measure before optimising (the J-031 rule).

Times each part of a real turn separately, on the live stack:
  schema build   rebuilding the discriminated union every plan call
  plan           the model choosing one action
  quarantine     extracting untrusted tool output, on BOTH lanes
  tool           the tool itself
  tts            speech synthesis

Guessing which of these dominates is how you spend an afternoon making the
fast part faster.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import voice                                              # noqa: E402
from core.graph import SYSTEM_PROMPT, _proposed_action_model        # noqa: E402
from core.llm import LANES, _structured, quarantine_text            # noqa: E402
from core.tools import catalog, run_tool                            # noqa: E402

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
SB = ROOT / "sandbox"
probe = SB / "profile-probe.txt"
probe.write_text("Project notes.\nThe deadline is Friday.\nOwner: Mudit.\n", encoding="utf-8")


def t(fn, n=3):
    xs = []
    for _ in range(n):
        a = time.perf_counter()
        fn()
        xs.append(time.perf_counter() - a)
    return statistics.median(xs), min(xs), max(xs)


print(f"\n{'step':34} {'median':>8} {'min':>7} {'max':>7}")


def row(name, fn, n=3):
    m, lo, hi = t(fn, n)
    print(f"{name:34} {m:7.2f}s {lo:6.2f}s {hi:6.2f}s")
    return m


row("build action schema", lambda: _proposed_action_model(), 5)

MODEL = _proposed_action_model()
MSG = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nTOOLS:\n" + catalog()},
       {"role": "user", "content": f"Read {probe} and tell me what it says."}]


def plan_call():
    _structured.chat.completions.create(model=LANES["planner"], messages=MSG,
                                        response_model=MODEL, max_retries=1, extra_body=NO_THINK)


row("plan step (workhorse)", plan_call)

read = {"tool": "read_file", "args": {"path": str(probe)}}
row("tool: read_file", lambda: run_tool(read), 5)

content = probe.read_text()
row(f"quarantine {len(content)}ch (workhorse)", lambda: quarantine_text(content, "read_file output"))
row(f"quarantine {len(content)}ch (reflex)",
    lambda: quarantine_text(content, "read_file output", lane="reflex"))

big = (content + "Additional context line.\n") * 30
row(f"quarantine {len(big)}ch (workhorse)", lambda: quarantine_text(big, "read_file output"))
row(f"quarantine {len(big)}ch (reflex)", lambda: quarantine_text(big, "read_file output", lane="reflex"))

row("tts 'The deadline is Friday.'", lambda: voice.synth("The deadline is Friday."), 3)

print("\n  A read-and-answer turn = 2 plan steps + 1 tool + 1 quarantine + 2 tts.")
