"""
Model downloads, staged.

    python scripts/get_models.py            # tier 1 only: reflex, ~2.5 GB
    python scripts/get_models.py --all      # the whole bench, ~43 GB
    python scripts/get_models.py --tier 2   # up to and including tier 2

STAGED ON PURPOSE. Do not download 43 GB to find out the plumbing is broken.
The reflex model alone proves llama.cpp runs on this GPU, the server starts,
llama-swap rotates, LiteLLM routes and the graph executes a tool. Then fetch the
rest knowing the stack is sound.

Uses the huggingface_hub Python API rather than the CLI: the CLI was renamed
between hub 0.x and 1.x (`huggingface-cli` -> `hf`) and the old module path no
longer exists. The API is stable and this file is cross-platform, unlike a .ps1.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"

# tier, label, repo, glob, approx GB
BENCH = [
    (1, "reflex    - pinned classifier",  "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF", "*Q4_K_M.gguf", 2.5),
    (2, "workhorse - coder and planner",  "unsloth/Qwen3.6-35B-A3B-GGUF",               "*Q4_K_M*.gguf", 22.1),
    (3, "tools     - function calling",   "unsloth/GLM-4.7-Flash-GGUF",                 "*Q4_K_XL*.gguf", 17.5),
    (3, "vision    - screenshots",        "Qwen/Qwen3-VL-8B-Instruct-GGUF",             "*.gguf",          6.0),
    (3, "writer    - email and posts",    "bartowski/google_gemma-4-31B-it-GGUF",       "*Q4_K_M*.gguf",  18.5),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=1)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    max_tier = 3 if args.all else args.tier

    # Xet is huggingface_hub 1.x's default transfer backend. On this machine it
    # opened the file and then sat at 0 bytes indefinitely - no error, no
    # progress, just a stalled .incomplete. Plain HTTPS range requests work
    # fine. If you want to try Xet again later, unset this and watch the file
    # size for the first 30 seconds rather than trusting the progress bar.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)  # deprecated in hub 1.x

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub missing - run scripts/01-setup-python.ps1 first")
        return 1

    MODELS.mkdir(parents=True, exist_ok=True)
    wanted = [b for b in BENCH if b[0] <= max_tier]
    total = sum(b[4] for b in wanted)
    print(f"\n{len(wanted)} model(s), about {total:.1f} GB, into {MODELS}\n")

    for tier, label, repo, pattern, gb in wanted:
        print(f"--- tier {tier}  {label}  (~{gb} GB)")
        print(f"    {repo}  [{pattern}]")
        try:
            snapshot_download(
                repo_id=repo,
                allow_patterns=[pattern],
                local_dir=str(MODELS),
                max_workers=4,
            )
            print("    done\n")
        except Exception as e:
            print(f"    FAILED: {type(e).__name__}: {e}\n")

    print("--- on disk ---")
    found = sorted(MODELS.rglob("*.gguf"))
    if not found:
        print("  nothing")
    for p in found:
        print(f"  {p.name:<58} {p.stat().st_size / 1024**3:>6.1f} GB")
    if max_tier < 3:
        print(f"\n  Tier {max_tier} only. Run with --all for the full bench.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
