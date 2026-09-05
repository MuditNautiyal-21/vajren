"""06 - End-to-end smoke test.

Proves the whole local chain: HTTP -> LiteLLM :4000 -> llama-server -> weights
-> tokens back. Until this passes, everything above it is theory.

Measures first-token latency and generation rate separately, because they fail
for different reasons: slow first token means the prompt is being processed on
the wrong device, slow generation means the weights are.

    .venv\\Scripts\\python.exe scripts\\06-smoke-test.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:4000/v1/chat/completions"

# (route, prompt, max_tokens). Routes are the aliases LiteLLM exposes; see
# config/litellm.yaml. Only routes whose weights are on disk will answer.
# ⚠ The workhorse is a REASONING model. It emits its chain of thought as
#   `reasoning_content` deltas before a single `content` delta appears, and that
#   thinking counts against max_tokens. At 160 tokens it spent the entire budget
#   thinking and returned an empty answer - which looks exactly like a broken
#   backend. Budget for the thinking, and read both fields.
CASES = [
    ("vajren-reflex", "Reply with exactly one word: ready", 16),
    ("vajren-workhorse",
     "In two sentences, what is the difference between a deadlock and a livelock?", 800),
]


def call(route: str, prompt: str, max_tokens: int) -> tuple[bool, str, float, float, int, int]:
    """Returns (ok, text, ttft_s, total_s, n_content_chunks, n_thinking_chunks)."""
    body = json.dumps({
        "model": route,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
    }).encode()

    req = urllib.request.Request(
        GATEWAY, data=body,
        headers={"Content-Type": "application/json",
                 # LiteLLM wants a key even when everything behind it is local.
                 "Authorization": "Bearer sk-vajren-local"},
    )

    t0 = time.perf_counter()
    ttft = float("nan")
    chunks: list[str] = []
    thinking = 0
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0]["delta"]
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta.get("reasoning_content"):
                    thinking += 1
                    if thinking == 1:
                        ttft = time.perf_counter() - t0
                piece = delta.get("content") or ""
                if piece:
                    if not chunks and thinking == 0:
                        ttft = time.perf_counter() - t0
                    chunks.append(piece)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}", 0.0, 0.0, 0, 0
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", 0.0, 0.0, 0, 0

    total = time.perf_counter() - t0
    text = "".join(chunks)
    return bool(text), text, ttft, total, len(chunks), thinking


def main() -> int:
    failures = 0
    for route, prompt, max_tokens in CASES:
        print(f"\n--- {route} ---")
        ok, text, ttft, total, n, thinking = call(route, prompt, max_tokens)
        if not ok:
            print(f"  FAIL  {text}")
            failures += 1
            continue
        emitted = n + thinking
        gen = (emitted - 1) / (total - ttft) if emitted > 1 and total > ttft else float("nan")
        print(f"  first token  {ttft:6.2f} s")
        print(f"  generation   {gen:6.1f} tok/s   ({emitted} chunks in {total:.2f} s)")
        if thinking:
            print(f"  thought for  {thinking} chunks before answering")
        print(f"  said: {text.strip()[:300]}")

    print()
    if failures:
        print(f"{failures} of {len(CASES)} routes FAILED")
        return 1
    print(f"all {len(CASES)} routes answered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
