"""07 - Talk to the workhorse llama-server directly, bypassing LiteLLM.

When a request fails through the gateway the question is always the same: is it
the gateway or is it the model? This asks the model straight, non-streaming, and
prints the WHOLE response object so nothing gets hidden by a client that only
looks at choices[0].message.content - reasoning models put their output
somewhere else entirely.
"""
from __future__ import annotations

import json
import time
import urllib.request

URL = "http://127.0.0.1:8081/v1/chat/completions"

body = json.dumps({
    "model": "workhorse",
    "messages": [{"role": "user", "content": "Say the single word: ready"}],
    "max_tokens": 200,
    "temperature": 0.2,
}).encode()

req = urllib.request.Request(
    URL, data=body,
    headers={"Content-Type": "application/json", "Authorization": "Bearer sk-local"},
)

t0 = time.perf_counter()
with urllib.request.urlopen(req, timeout=600) as resp:
    data = json.loads(resp.read().decode("utf-8", "replace"))
dt = time.perf_counter() - t0

print(f"round trip: {dt:.1f} s\n")
print(json.dumps(data, indent=2)[:4000])

usage = data.get("usage") or {}
if usage:
    out = usage.get("completion_tokens") or 0
    print(f"\ncompletion tokens: {out}   -> {out / dt:.1f} tok/s")
