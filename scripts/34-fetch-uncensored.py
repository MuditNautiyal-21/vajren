"""34 - Fetch the 'uncensored' candidates into models/ as flat .gguf files.

Runs detached; watch logs\fetch-final.log. Sequential on purpose: the HF CDN
measured ~1 MB/s from this machine on 2026-09-06, so two parallel pulls just
halve each other.

  A) Qwen3.8-27B-Uncensored  Q4_K_M  16.8 GB  DENSE 27B   <- Mudit asked for this
     ⚠ orcarouter/...-GGUF is a GATED repo (403 even signed in as Dragon-21),
       so this pulls the same weights from chimingw's ungated mirror, which is
       an unofficial conversion of orcarouter/Qwen3.8-27B-Uncensored-FP8.
     ⚠ DENSE. All 27B active every token. 16.8 GB will not fit a 12 GB card,
       so it spills to the CPU exactly as Gemma-4-31B did: 2.36 tok/s (J-048).
       Downloaded to MEASURE. Bench before wiring it into llama-swap.

  B) gemma-4-26B-A4B-it-uncensored  Q4_K_M  ~16 GB  MoE, ~4B ACTIVE
     The candidate that can actually be fast: same shape as the 35B-A3B
     workhorse that gets 30.8 tok/s on this card.

Neither model generates images or video. Both are text+vision-IN, text-OUT.
llama.cpp cannot generate pixels at all - that needs a diffusion stack.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = Path(r"C:\vajren\models")
STAGE = MODELS / ".fetch"

JOBS = [
    ("qwen38-27b-dense", "chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF",
     ["*Q4_K_M*.gguf"]),
    ("gemma4-26b-a4b-moe", "mradermacher/gemma-4-26B-A4B-it-uncensored-GGUF",
     ["*Q4_K_M*.gguf"]),
]

for tag, repo, patterns in JOBS:
    print(f"\n=== {tag}  <- {repo}  {patterns}", flush=True)
    t = time.perf_counter()
    try:
        got = snapshot_download(repo_id=repo, allow_patterns=patterns,
                                local_dir=STAGE / tag, max_workers=4)
        files = sorted(Path(got).rglob("*.gguf"))
        if not files:
            print("  NOTHING MATCHED - check the repo's file list", flush=True)
            continue
        for f in files:
            # Flat in models/, like every other weight here, so llama-swap
            # config paths stay one level deep.
            dest = MODELS / f.name
            if dest.exists() and dest.stat().st_size == f.stat().st_size:
                print(f"  already in place: {dest.name}", flush=True)
                continue
            shutil.move(str(f), str(dest))
            print(f"  PLACED  {dest.name:58} {dest.stat().st_size/1e9:6.1f} GB", flush=True)
        print(f"  done in {time.perf_counter() - t:.0f}s", flush=True)
    except Exception as e:                                   # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {str(e)[:300]}", flush=True)

print("\nALL JOBS FINISHED", flush=True)
