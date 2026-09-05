"""17c - Replay what it heard, through different ears.

With no arguments: synthesizes a few phrases with the name in them, adds
noise, and compares STT settings — this is the measurement behind the
beam-5 + initial_prompt choice in core/voice.py.

With WAV paths (logs/utterances/*.wav — every utterance is saved now):
transcribes the real recording with the current settings and with the old
ones, and prints loudness, so "it heard me wrong" is diagnosable instead of
argued about.

    .venv\\Scripts\\python.exe -X utf8 scripts\\17c-stt-settings.py
    .venv\\Scripts\\python.exe -X utf8 scripts\\17c-stt-settings.py logs\\utterances\\*.wav
"""
from __future__ import annotations

import glob
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import voice                                  # noqa: E402

m = voice._load_stt()
VARIANTS = {
    "old (greedy, no prompt)": dict(vad_filter=True, beam_size=1, condition_on_previous_text=False),
    "now (beam5 + names)":     dict(vad_filter=True, beam_size=5, condition_on_previous_text=False,
                                    initial_prompt=voice._names_prompt(), hotwords="Vajren",
                                    vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300}),
    "no VAD":                  dict(vad_filter=False, beam_size=5, condition_on_previous_text=False,
                                    initial_prompt=voice._names_prompt()),
}


def run(audio, **kw):
    t = time.perf_counter()
    segs = list(m.transcribe(audio, language="en", **kw)[0])
    dt = time.perf_counter() - t
    text = " ".join(s.text.strip() for s in segs)
    lp = sum(s.avg_logprob for s in segs) / max(len(segs), 1)
    return text, round(math.exp(lp), 2), round(dt, 2)


def show(name, audio):
    rms = float(np.sqrt(np.mean(audio ** 2))); peak = float(np.max(np.abs(audio)))
    loud = "QUIET — mic gain?" if peak < 0.05 else ("clipping" if peak > 0.99 else "ok")
    print(f"\n>> {name}   {len(audio)/16000:.1f}s  rms={rms:.4f} peak={peak:.3f} ({loud})")
    for label, kw in VARIANTS.items():
        text, conf, dt = run(audio, **kw)
        print(f"   {label:24s} {conf:.2f} {dt:4.1f}s  {text!r}")


files = [f for a in sys.argv[1:] for f in glob.glob(a)]
if files:
    import soundfile as sf
    for f in files[-12:]:
        a, sr = sf.read(f, dtype="float32")
        if a.ndim > 1:
            a = a.mean(axis=1)
        if sr != 16000:
            n = int(len(a) * 16000 / sr)
            a = np.interp(np.linspace(0, len(a), n, endpoint=False), np.arange(len(a)), a).astype(np.float32)
        show(Path(f).name, a)
else:
    rng = np.random.default_rng(0)
    for p in ["Hey Vajren, do you have a second name?",
              "Vajren, open notepad and write my name ten times",
              "is your name Vajren or Vajran?", "yes go ahead"]:
        s, sr = voice.synth(p)
        n = int(len(s) * 16000 / sr)
        a = np.interp(np.linspace(0, len(s), n, endpoint=False), np.arange(len(s)), s).astype(np.float32)
        show(p, (a + rng.normal(0, 0.02, len(a))).astype(np.float32))
