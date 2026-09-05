"""16 - Fetch the voice models.

  Kokoro v1.0   TTS, 82M params, ONNX, Apache-2.0. Runs on the CPU in real time.
                Chosen over Piper for quality and over anything cloud for the
                obvious reason: a voice assistant that phones home is not local.
  faster-whisper  STT, CTranslate2. small.en int8 on CPU.

Both land in models/voice/ rather than a user-profile cache, so the whole
assistant stays inside C:\\vajren and survives the format Mudit does every
few months (J-022).

Resumable, because a 310 MB download that dies at 300 MB and starts over is
how you lose an evening.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

# ⚠ Set BEFORE huggingface_hub is imported anywhere. Hugging Face's Xet transfer
#   backend stalls at 0 bytes on this machine — it does not error, it does not
#   time out, it just sits there forever holding an .incomplete file. Cost an
#   evening the first time (J-028); cost ten minutes the second time, because it
#   looks exactly like a slow download. Now it cannot happen a third time.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "models" / "voice"
VOICE.mkdir(parents=True, exist_ok=True)

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = [("kokoro-v1.0.onnx", 310_000_000), ("voices-v1.0.bin", 26_000_000)]


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {u}"
        n /= 1024
    return f"{n:,.1f} TB"


def fetch(url: str, dest: Path, attempts: int = 50) -> bool:
    for attempt in range(1, attempts + 1):
        have = dest.stat().st_size if dest.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "vajren"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length", 0)) + have
                mode = "ab" if have and r.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                t0, last = time.time(), have
                with open(dest, mode) as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - t0 > 3:
                            rate = (have - last) / (time.time() - t0)
                            pct = f"{have / total * 100:5.1f}%" if total else "  ?  "
                            print(f"\r    {pct}  {human(have)}  ({human(rate)}/s)   ",
                                  end="", flush=True)
                            t0, last = time.time(), have
            print(f"\r    done  {human(dest.stat().st_size)}                    ")
            return True
        except Exception as e:                                    # noqa: BLE001
            print(f"\r    attempt {attempt}: {type(e).__name__} - resuming in 5s   ")
            time.sleep(5)
    return False


ok = True
for name, expect in FILES:
    dest = VOICE / name
    if dest.exists() and dest.stat().st_size > expect * 0.9:
        print(f"  have  {name}  ({human(dest.stat().st_size)})")
        continue
    print(f"  get   {name}")
    ok &= fetch(BASE + name, dest)

# ⚠ Whisper is fetched by URL, NOT through huggingface_hub.
#   hf_hub stalls at 0 bytes on this machine and never times out — it holds an
#   .incomplete file forever, looking exactly like a slow download. Setting
#   HF_HUB_DISABLE_XET did not help. This is the second time a Hugging Face
#   transfer has cost real time here (J-028), and the fix was the same then:
#   use the downloader we control. WhisperModel accepts a local directory, so
#   nothing downstream cares.
WHISPER = VOICE / "whisper" / "small.en"
WHISPER.mkdir(parents=True, exist_ok=True)
HF = "https://huggingface.co/Systran/faster-whisper-small.en/resolve/main/"
# ⚠ Exactly the four files this repo has — verified against the HF API, not
#   guessed. A guessed 5th ("preprocessor_config.json") 404'd, and curl dutifully
#   retried the 404 twenty times before giving up, which reads as a hang. This
#   is the same lesson as the .gguf filenames: check the API, do not assume.
#   Sizes are the ACTUAL bytes on disk after a good download, not estimates —
#   the range check below is meaningless if the centre of the range is a guess.
WFILES = [("model.bin", 483_546_902), ("config.json", 2_657),
          ("tokenizer.json", 2_128_466), ("vocabulary.txt", 422_309)]

def fetch_curl(url: str, dest: Path) -> bool:
    """
    ⚠ curl, not urllib, for Hugging Face.

    urllib got URLError then TimeoutError on every attempt against these URLs
    while curl fetched the same path with a plain 200. HF answers `resolve/main`
    with a redirect to a signed CDN URL carrying a query string; curl.exe (built
    into Windows 10+) follows it, resumes with -C -, and retries on its own.
    Not worth reimplementing that to keep the dependency count at zero.
    """
    import subprocess
    cmd = ["curl.exe", "-L", "--fail", "--retry", "8", "--retry-delay", "4",
           "--retry-all-errors", "-C", "-", "--connect-timeout", "30",
           "--max-time", "2400",          # a hang must end eventually
           "-A", "vajren", "-o", str(dest), url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 or (dest.exists() and dest.stat().st_size > 0
                             and r.returncode == 33):   # 33 = range not supported
        print(f"    done  {human(dest.stat().st_size)}")
        return True
    print(f"    curl exit {r.returncode}: {r.stderr.strip()[:200]}")
    return False


print("\n  whisper small.en (int8):")
for name, expect in WFILES:
    dest = WHISPER / name
    # ⚠ A RANGE, not a floor. Two different corruptions were accepted by
    #   size checks here in one evening:
    #     min(expect*0.9, 90) -> 90 bytes, so a truncated 30 MB file "passed"
    #     >= expect*0.9       -> a 684 MB file "passed" a 484 MB expectation,
    #                            because curl -C - resumed from a bad offset and
    #                            appended a whole second copy onto a partial.
    #   Too big is as broken as too small. Both load far enough to fail
    #   confusingly ("UnicodeDecodeError at position 13") rather than cleanly.
    size = dest.stat().st_size if dest.exists() else -1
    if expect * 0.9 <= size <= expect * 1.25:
        print(f"  have  {name}  ({human(size)})")
        continue
    if dest.exists():
        # ⚠ Never resume a file another tool started. -C - trusts the existing
        #   bytes, and after a failed urllib attempt nobody knows what they are.
        print(f"  discard {name} ({human(size)}, expected ~{human(expect)})")
        dest.unlink()
    print(f"  get   {name}  (curl)")
    ok &= fetch_curl(HF + name, dest)

try:
    from faster_whisper import WhisperModel
    WhisperModel(str(WHISPER), device="cpu", compute_type="int8")
    print("  whisper loads")
except Exception as e:                                            # noqa: BLE001
    print(f"  whisper FAILED to load: {type(e).__name__}: {e}")
    ok = False

print("\n  models/voice/ now holds:")
for p in sorted(VOICE.rglob("*")):
    if p.is_file() and p.stat().st_size > 1_000_000:
        print(f"    {str(p.relative_to(VOICE)):<48} {human(p.stat().st_size)}")
sys.exit(0 if ok else 1)
