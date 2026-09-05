"""12 - Does llama-swap actually rotate models on one card?

Three models, 12 GB. The card holds one ~20 GB-class model at a time, so the
whole bench depends on this working. Asks reflex, then workhorse, then tools,
then workhorse AGAIN - the last one is the point: it proves a model can come
back after being evicted, which is the case that fails if a group is misconfigured.

Prints the load cost separately from the answer cost, because a swap is paid in
load time and that is the number that decides whether to batch by lane.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

GATEWAY = "http://127.0.0.1:4000/v1/chat/completions"
SEQ = [
    ("vajren-reflex",    "Reply with one word: ready"),
    ("vajren-workhorse", "Reply with one word: ready"),
    ("vajren-tools",     "Reply with one word: ready"),
    ("vajren-workhorse", "Reply with one word: ready"),   # evicted, must return
]


def ask(route: str, prompt: str) -> tuple[bool, str, float]:
    body = json.dumps({"model": route, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 600, "temperature": 0.1,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(GATEWAY, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer sk-vajren-local"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        msg = d["choices"][0]["message"]
        return True, (msg.get("content") or "").strip()[:60], time.perf_counter() - t0
    except Exception as e:                                        # noqa: BLE001
        detail = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else str(e)
        return False, f"{type(e).__name__}: {detail}", time.perf_counter() - t0


fails = 0
print(f"\n{'route':20} {'seconds':>9}   result")
for i, (route, prompt) in enumerate(SEQ, 1):
    ok, text, dt = ask(route, prompt)
    note = "  <- returning after eviction" if i == len(SEQ) else ""
    print(f"{route:20} {dt:8.1f}s   {'ok  ' if ok else 'FAIL'} {text}{note}")
    fails += 0 if ok else 1

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
