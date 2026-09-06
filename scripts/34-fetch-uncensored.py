"""34 - Fetch the two 'uncensored' candidates and report what landed.

Runs detached; watch logs\fetch-uncensored.log.

  A) orcarouter/Qwen3.8-27B-Uncensored-GGUF   IQ4_XS  ~15.3 GB  DENSE 27B
     The one Mudit asked for. The MLX build he linked cannot load on an AMD
     card at all (Apple-only format), so this is the runnable equivalent.
     Expected to be slow for the same reason Gemma-4-31B was (J-048): dense
     weights larger than 12 GB spill to the CPU. Downloaded to MEASURE, not
     because it is expected to win.

  B) mradermacher/gemma-4-26B-A4B-it-uncensored-GGUF   Q4_K_M  ~16 GB  MoE A4B
     Found while checking (A). 26B total but only ~4B active per token - the
     same shape as the 35B-A3B workhorse that gets 30.8 tok/s on this card.
     This is the candidate that can actually be fast.
"""
from __future__ import annotations

import sys, time
from pathlib import Path
from huggingface_hub import snapshot_download

DEST = Path(r"C:\vajren\models")
JOBS = [
    ("orcarouter/Qwen3.8-27B-Uncensored-GGUF", ["*IQ4_XS*", "*mmproj*f16*"], "qwen38-27b-dense"),
    ("mradermacher/gemma-4-26B-A4B-it-uncensored-GGUF", ["*Q4_K_M*"], "gemma4-26b-a4b-moe"),
]

for repo, patterns, tag in JOBS:
    print(f"\n=== {tag}  <- {repo}  {patterns}", flush=True)
    t = time.perf_counter()
    try:
        p = snapshot_download(repo_id=repo, allow_patterns=patterns,
                              local_dir=DEST / tag, max_workers=4)
        got = sorted(Path(p).rglob("*.gguf"))
        for f in got:
            print(f"  {f.name:60} {f.stat().st_size/1e9:6.1f} GB", flush=True)
        if not got:
            print("  NOTHING MATCHED - patterns wrong, check the repo file list", flush=True)
        print(f"  done in {time.perf_counter()-t:.0f}s", flush=True)
    except Exception as e:                                   # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {e}", flush=True)

print("\nALL JOBS FINISHED", flush=True)
