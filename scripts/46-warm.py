"""46 - Warm a lane with the prompt it will actually see.

⚠ THE OLD WARM-UP SENT "hi".

That loads the weights — which is what the ~20 s was — and leaves the KV cache
holding two tokens. The first REAL request then prefilled the entire static
head from scratch. Measured on his own machine, 2026-09-08:

    turn 1   8,893 prompt tokens, cached 0      prefill 79.0 s   total 105.8 s
    turn 2   8,879 prompt tokens, cached 7,953  prefill 11.4 s   total  15.3 s

Same sentence, seven times the wall clock, and the only difference was whether
the cache already held the head. He described the result as "too slow, very
very slow", and he was right: he was paying 79 s to tell the model who it is,
once per cold start, every time.

Prefix caching is EXACT-MATCH, so this must send byte-for-byte what plan()
sends. It is therefore built from the same functions — SYSTEM_PROMPT, catalog()
and _proposed_action_model() — and never from a copy of them. If someone
changes the prompt and not this file, the prefix stops matching and the warm-up
silently becomes decorative again; importing rather than duplicating is what
stops that.

    .venv\\Scripts\\python.exe -X utf8 scripts\\46-warm.py [lane]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.graph import SYSTEM_PROMPT, _proposed_action_model      # noqa: E402
from core.llm import structured                                   # noqa: E402
from core.tools import catalog                                    # noqa: E402

lane = sys.argv[1] if len(sys.argv) > 1 else "private"

head = SYSTEM_PROMPT + "\n\nTOOLS:\n" + catalog()
messages = [{"role": "system", "content": head},
            # ⚠ Deliberately a real, answerable request. A blank or nonsense
            #   turn makes the model retry against the schema, which is slower
            #   and no warmer — the prefix is cached either way, but a clean
            #   answer keeps the timing honest and proves the lane works.
            {"role": "user", "content": "Say hello. Nothing else."}]

t0 = time.perf_counter()
ok = True
try:
    structured(messages, _proposed_action_model(), lane=lane)
except Exception as exc:                                          # noqa: BLE001
    # A schema retry or a refused answer still leaves the head in the cache,
    # which is the whole point. Only a dead lane is worth reporting as failure.
    ok = "Connection" not in type(exc).__name__ and "Timeout" not in type(exc).__name__
    print(f"  warm-up answer discarded ({type(exc).__name__})")

took = time.perf_counter() - t0
print(f"  {lane:<8} warm in {took:5.1f}s  ({len(head):,} chars of prefix now cached)")
sys.exit(0 if ok else 1)
