"""
Direct, resumable model download.

    python scripts/fetch.py reflex
    python scripts/fetch.py workhorse
    python scripts/fetch.py --all

Why not huggingface_hub: on this machine its downloader hung twice - once with
the Xet backend (0 bytes, no error) and once with Xet disabled (stopped at
5.7 MB with the process idle at 0% CPU). Meanwhile a raw HTTP range request to
the same URL pulled 10 MB fine. So this does the simple thing: one HTTP GET with
a Range header, straight to disk, resuming from whatever is already there.

Resumable matters here. Measured throughput to the HF CDN on this connection is
about 0.64 MB/s, which makes the full bench roughly a 19-hour download. It has
to survive being interrupted, and it has to be safe to just run again.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
BASE = "https://huggingface.co/{repo}/resolve/main/{file}"

MODELS_MAP = {
    "reflex":    ("bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF",
                  "Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
    # Filenames verified against the HF API 2026-09-04. Do not guess these -
    # 'Qwen3.6-35B-A3B-Q4_K_M.gguf' looks right and 404s; the real one is UD-.
    "workhorse": ("unsloth/Qwen3.6-35B-A3B-GGUF",
                  "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"),
    "tools":     ("unsloth/GLM-4.7-Flash-GGUF",
                  "GLM-4.7-Flash-UD-Q4_K_XL.gguf"),
    "writer":    ("bartowski/google_gemma-4-31B-it-GGUF",
                  "google_gemma-4-31B-it-Q4_K_M.gguf"),
    "vision":    ("Qwen/Qwen3-VL-8B-Instruct-GGUF",
                  "Qwen3VL-8B-Instruct-Q4_K_M.gguf"),
}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"


def fetch_with_retry(name: str, max_attempts: int = 500) -> bool:
    """
    A 24-hour download over a link that drops is not one download, it is a few
    hundred. The first version of this reported 'interrupted, run again to
    resume' and stopped - technically honest and practically useless, because
    nobody is awake to run it again at 3am.

    So: retry forever-ish, resuming from disk each time, with a short backoff so
    a genuinely dead network does not spin. Every attempt makes progress or
    costs a few seconds.
    """
    for attempt in range(1, max_attempts + 1):
        if fetch(name):
            return True
        done = (MODELS / MODELS_MAP[name][1]).exists()
        if done:
            return True
        wait = min(60, 5 * attempt)
        print(f"[{name}] attempt {attempt} ended early; retrying in {wait}s", flush=True)
        time.sleep(wait)
    print(f"[{name}] gave up after {max_attempts} attempts")
    return False


def fetch(name: str) -> bool:
    repo, fname = MODELS_MAP[name]
    url = BASE.format(repo=repo, file=fname)
    dest = MODELS / fname
    part = dest.with_suffix(dest.suffix + ".part")
    MODELS.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        print(f"[{name}] already here: {dest.name} ({human(dest.stat().st_size)})")
        return True

    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "vajren/0.1"})
    if have:
        req.add_header("Range", f"bytes={have}-")
        print(f"[{name}] resuming from {human(have)}")

    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except Exception as e:
        print(f"[{name}] connect failed at {human(have)}: {type(e).__name__}: {e}", flush=True)
        return False

    total = have + int(resp.headers.get("Content-Length", 0))
    print(f"[{name}] {fname}  ->  {human(total)}")

    start, last, last_bytes = time.time(), 0.0, have
    try:
        with open(part, "ab") as f:
            while True:
                chunk = resp.read(1 << 20)          # 1 MB
                if not chunk:
                    break
                f.write(chunk)
                have += len(chunk)
                now = time.time()
                if now - last > 10:                 # progress every 10s, not every chunk
                    rate = (have - last_bytes) / (now - last) if last else 0
                    eta = (total - have) / rate / 60 if rate > 0 else 0
                    pct = have / total * 100 if total else 0
                    print(f"  {pct:5.1f}%  {human(have)} / {human(total)}  "
                          f"{rate/1024/1024:.2f} MB/s  eta {eta:,.0f} min", flush=True)
                    last, last_bytes = now, have
    except Exception as e:
        print(f"[{name}] interrupted at {human(have)}: {type(e).__name__}: {e}")
        print(f"[{name}] run again to resume.")
        return False

    part.rename(dest)
    mins = (time.time() - start) / 60
    print(f"[{name}] done: {human(dest.stat().st_size)} in {mins:.1f} min\n")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", nargs="*", default=["reflex"])
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    names = list(MODELS_MAP) if a.all else a.which
    ok = True
    for n in names:
        if n not in MODELS_MAP:
            print(f"unknown: {n}. options: {', '.join(MODELS_MAP)}")
            ok = False
            continue
        ok &= fetch_with_retry(n)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
