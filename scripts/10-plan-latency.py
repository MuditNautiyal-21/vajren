"""10 - Where does a plan step actually spend its time?

Each plan step costs 6-14 s. Before routing anything anywhere, find out WHY.
Two candidate causes, and they call for opposite fixes:

  the model is big      -> route simple steps to the 4B reflex model
  the model is THINKING -> keep the 35B, turn thinking off for this call

Qwen3.6 emitted 578 reasoning chunks before a two-sentence answer (J-029). For
a schema-constrained single tool call there is nothing to reason about, so the
second hypothesis is the likely one — but likely is not measured.

Runs the REAL plan schema (the discriminated union over all six tools) so the
numbers mean something.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.graph import SYSTEM_PROMPT, _proposed_action_model   # noqa: E402
from core.llm import LANES, _structured                        # noqa: E402
from core.tools import catalog                                 # noqa: E402

MODEL = _proposed_action_model()
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}

TASKS = [
    "Read the file C:\\vajren\\sandbox\\loop-probe.txt and tell me what it says.",
    "List everything in C:\\vajren\\scripts.",
    "Write 'hello' to C:\\vajren\\sandbox\\hello.txt",
]


def once(lane: str, prompt: str, extra: dict | None) -> tuple[float, str, bool]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nTOOLS:\n" + catalog()},
            {"role": "user", "content": prompt}]
    t0 = time.perf_counter()
    try:
        r = _structured.chat.completions.create(
            model=LANES[lane], messages=msgs, response_model=MODEL,
            max_retries=1, extra_body=extra or {})
        return time.perf_counter() - t0, r.action.tool, True
    except Exception as e:
        return time.perf_counter() - t0, f"{type(e).__name__}", False


CONFIGS = [
    ("workhorse, thinking ON  ", "planner", None),
    ("workhorse, thinking OFF ", "planner", NO_THINK),
    ("reflex 4B, thinking OFF ", "reflex", NO_THINK),
]

print(f"\n{'config':26} {'median':>8} {'range':>16}   tools chosen")
for label, lane, extra in CONFIGS:
    times, tools, ok = [], [], 0
    for t in TASKS:
        dt, tool, good = once(lane, t, extra)
        times.append(dt)
        tools.append(tool)
        ok += good
    print(f"{label:26} {statistics.median(times):7.1f}s "
          f"{min(times):6.1f}-{max(times):5.1f}s   {ok}/{len(TASKS)} ok  {', '.join(tools)}")

print("\nExpected tools: read_file, list_directory, write_file")
