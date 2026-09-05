"""17b - What does Whisper's confidence actually look like on this machine?

min_stt_confidence in policy.yaml was 0.75, chosen before any confidence
existed to measure. Clean synthesized "yes go ahead" scores ~0.63. So either
the threshold moves or voice never approves anything.

This measures the two populations the threshold has to separate:
  CLEAN   Kokoro speech, as close to a good headset take as we can make
  NOISY   the same speech under white noise at falling SNR, until Whisper
          starts getting the words wrong - THAT is the boundary that matters:
          confidence must fall below the gate before the words go wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import voice                                          # noqa: E402

rng = np.random.default_rng(7)
PHRASES = ["yes go ahead", "cancel", "confirmed go ahead", "no stop",
           "read the notes file and tell me what it says"]


def norm(s):
    import re
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def to16k(samples, sr):
    n = int(len(samples) * 16000 / sr)
    return np.interp(np.linspace(0, len(samples), n, endpoint=False),
                     np.arange(len(samples)), samples).astype(np.float32)


print(f"\n{'phrase':46} {'snr':>6}  {'conf':>5}  heard")
clean, wrong = [], []
for p in PHRASES:
    s, sr = voice.synth(p)
    x = to16k(s, sr)
    for snr_db in (None, 20, 10, 5, 0, -5):
        y = x if snr_db is None else x + rng.normal(0, np.sqrt(np.mean(x**2)) / (10 ** (snr_db / 20)), len(x)).astype(np.float32)
        text, conf = voice.transcribe_scored(y)
        ok = norm(text) == norm(p) or norm(p) in norm(text)
        tag = "clean" if snr_db is None else f"{snr_db:>3}dB"
        print(f"{p:46} {tag:>6}  {conf:5.2f}  {'  ' if ok else 'X '}{text!r}")
        (clean if snr_db is None else (wrong if not ok else [])).append(conf)

print(f"\n  clean speech confidence:  min {min(clean):.2f}  mean {np.mean(clean):.2f}")
if wrong:
    print(f"  WRONG transcriptions:     max {max(wrong):.2f}  mean {np.mean(wrong):.2f}")
    print(f"\n  a usable gate sits above {max(wrong):.2f} and below {min(clean):.2f}")
else:
    print("  no wrong transcriptions even at -5 dB - the gate is bounded above by "
          f"{min(clean):.2f} only")
