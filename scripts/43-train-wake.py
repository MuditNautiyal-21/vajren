"""43 - Train a custom wake word ("hey Vajren") with NO downloads.

    .venv\\Scripts\\python.exe -X utf8 scripts\\43-train-wake.py

WHY NOT THE OFFICIAL PIPELINE: openWakeWord's trainer wants piper-sample-
generator, speechbrain, audiomentations and ~GB of noise datasets - hours at
this box's 1 MB/s. Everything it needs is already here in another form:

  - Kokoro TTS (54 voices, local) says the phrase with real prosody variety;
  - openWakeWord's own feature extractor turns any clip into a 16x96 window;
  - openWakeWord's custom models ARE a small net on that window, so a net
    trained here and exported to ONNX loads with `Model(wakeword_models=[..])`
    and core/wake.py needs exactly one string changed.

HARD NEGATIVES MATTER MORE THAN POSITIVES. "hey Warren", "hey Karen",
"hey Aaron", "hey Jarvis", "hey Darren" are phonetically adjacent and are what
a lazy classifier confuses. They are generated in every voice too.

Output: models/wake/hey_vajren.onnx  +  a printed report with held-out
accuracy, false-positive rate on hard negatives, and ambient-mic false hits.
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "models" / "wake"
OUT.mkdir(parents=True, exist_ok=True)
random.seed(7); np.random.seed(7)

SR = 16000
WIN = 16                     # openWakeWord window: 16 frames x 96 dims (~1.28 s)

# ⚠ Mudit, 2026-09-06: "don't make 'hey vajren' the wake word, just vajren."
#   The bare name is the trigger. It still answers to "hey Vajren" / "okay
#   Vajren" because those contain it. The price is that any mention of the
#   name can wake it — the idle-only mute in core/wake.py is what keeps that
#   tolerable; it cannot wake itself saying its own name back to him.
POSITIVE = ["Vajren", "Vajren.", "Vajren!", "Vajren?", "Vajren,", "Vaj-ren", "Vajrenn",
            "Vahjren", "Vajran", "Vajrun", "hey Vajren", "okay Vajren", "Vajren, listen"]
HARD_NEG = ["Warren", "Karen", "Aaron", "Darren", "Jarvis", "Lauren", "Sharon", "Byron",
            "hey Warren", "hey Karen", "hey Jarvis", "hey Siri", "hey Google",
            "hey there", "hey", "okay", "region", "margin", "bargain", "virgin"]
SOFT_NEG = ["open the notepad", "what time is it", "call Mudit India on WhatsApp",
            "the weather is nice today", "yes go ahead", "no cancel that",
            "search youtube for lofi", "I asked you to reply to Sakshi",
            "one two three four five", "this is a test of the system",
            "play some music", "shut down the computer", "write an essay"]
SPEEDS = [0.85, 1.0, 1.15, 1.3]


def resample_24k_to_16k(x: np.ndarray) -> np.ndarray:
    n = int(len(x) * SR / 24000)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def augment(x: np.ndarray) -> np.ndarray:
    x = x * random.uniform(0.35, 1.3)                                    # gain
    if random.random() < 0.7:                                            # noise at random SNR
        snr = random.uniform(5, 30)
        p = np.mean(x ** 2) + 1e-9
        x = x + np.random.randn(len(x)).astype(np.float32) * np.sqrt(p / (10 ** (snr / 10)))
    if random.random() < 0.5:                                            # tiny pitch-ish warp
        r = random.uniform(0.94, 1.06)
        n = int(len(x) * r)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return np.clip(x, -1, 1)


def to_window(af, x: np.ndarray, place: str = "end") -> np.ndarray:
    """Pad/crop to ~2 s, embed, return the LAST 16 frames (what streaming sees)."""
    L = SR * 2
    if len(x) >= L:
        x = x[-L:] if place == "end" else x[:L]
    else:
        pad = L - len(x)
        lead = random.randint(0, pad) if place == "rand" else (pad if place == "end" else 0)
        x = np.concatenate([np.zeros(lead, np.float32), x, np.zeros(pad - lead, np.float32)])
    pcm = (x * 32767).astype(np.int16)
    e = af.embed_clips(pcm[None, :])[0]                                   # (frames, 96)
    return e[-WIN:] if e.shape[0] >= WIN else np.vstack([np.zeros((WIN - e.shape[0], 96)), e])


def main() -> None:
    from core import voice
    from core.voice import _state
    from openwakeword.utils import AudioFeatures
    voice._load_tts()
    tts = _state["tts"]
    voices = tts.get_voices()
    af = AudioFeatures(inference_framework="onnx")
    print(f"{len(voices)} voices, {len(POSITIVE)} positive phrasings, {len(HARD_NEG)} hard negatives")

    X, y, tags = [], [], []
    t0 = time.perf_counter()
    cache = OUT / "features.npz"
    if cache.exists() and "--fresh" not in sys.argv:
        z = np.load(cache, allow_pickle=True)
        X, y, tags = list(z["X"]), list(z["y"]), list(z["tags"])
        print(f"  loaded {len(X)} cached windows from {cache.name} (pass --fresh to regenerate)")

    def say(text, v, s):
        try:
            smp, sr = tts.create(text, voice=v, speed=s, lang="en-us")
            return resample_24k_to_16k(np.asarray(smp, np.float32))
        except Exception:                                                # noqa: BLE001
            return None

    generate = not X
    # positives: every voice x every phrasing x 2 speeds, augmented, window ends at speech end
    for v in (voices if generate else []):
        for ph in POSITIVE:
            for s in random.sample(SPEEDS, 2):
                a = say(ph, v, s)
                if a is None:
                    continue
                for _ in range(2):
                    X.append(to_window(af, augment(a), "end")); y.append(1); tags.append("pos")
    n_pos = len(X)
    print(f"  positives: {n_pos}  ({time.perf_counter() - t0:.0f}s)")

    if generate:
        # hard negatives: every voice x every hard phrase x 1 speed
        for v in voices:
            for ph in HARD_NEG:
                a = say(ph, v, random.choice(SPEEDS))
                if a is None:
                    continue
                X.append(to_window(af, augment(a), "end")); y.append(0); tags.append("hard")
        # soft negatives: a third of the voices x every sentence, random placement
        for v in random.sample(voices, max(6, len(voices) // 3)):
            for ph in SOFT_NEG:
                a = say(ph, v, random.choice(SPEEDS))
                if a is None:
                    continue
                X.append(to_window(af, augment(a), "rand")); y.append(0); tags.append("soft")
        # silence + pure noise
        for _ in range(120):
            lvl = random.choice([0.0, 0.002, 0.01, 0.05])
            X.append(to_window(af, np.random.randn(SR * 2).astype(np.float32) * lvl, "end")); y.append(0); tags.append("noise")
        print(f"  negatives: {len(X) - n_pos}  ({time.perf_counter() - t0:.0f}s)")
        np.savez_compressed(cache, X=np.asarray(X, np.float32), y=np.asarray(y, np.float32), tags=np.asarray(tags))
        print(f"  cached features -> {cache.name}")

    X = np.asarray(X, np.float32); y = np.asarray(y, np.float32); tags = np.asarray(tags)
    idx = np.random.permutation(len(X)); X, y, tags = X[idx], y[idx], tags[idx]
    n_te = len(X) // 5
    Xte, yte, tte = X[:n_te], y[:n_te], tags[:n_te]
    Xtr, ytr = X[n_te:], y[n_te:]

    import torch, torch.nn as nn
    torch.manual_seed(7)
    net = nn.Sequential(nn.Flatten(), nn.Linear(WIN * 96, 64), nn.ReLU(), nn.Dropout(0.2),
                        nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 1), nn.Sigmoid())
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    # positives are the minority; weight them so the net cannot win by saying "no"
    pw = torch.tensor([(len(ytr) - ytr.sum()) / max(ytr.sum(), 1)])
    lossf = nn.BCELoss(reduction="none")
    Xt, yt = torch.tensor(Xtr), torch.tensor(ytr)[:, None]
    for ep in range(60):
        net.train(); perm = torch.randperm(len(Xt))
        for i in range(0, len(Xt), 64):
            b = perm[i:i + 64]
            p = net(Xt[b]); w = torch.where(yt[b] > 0.5, pw, torch.ones(1))
            loss = (lossf(p, yt[b]) * w).mean()
            opt.zero_grad(); loss.backward(); opt.step()
    net.eval()
    with torch.no_grad():
        pte = net(torch.tensor(Xte)).numpy().ravel()
    pred = pte >= 0.5
    acc = (pred == (yte > 0.5)).mean()
    fp_hard = (pred[tte == "hard"]).mean() if (tte == "hard").any() else 0
    fp_soft = (pred[tte == "soft"]).mean() if (tte == "soft").any() else 0
    fp_noise = (pred[tte == "noise"]).mean() if (tte == "noise").any() else 0
    tp = (pred[yte > 0.5]).mean()
    print(f"\n  held-out accuracy      {acc:.3f}")
    print(f"  true positive rate     {tp:.3f}   (says yes to 'hey Vajren')")
    print(f"  false positives  hard  {fp_hard:.3f}   ('hey Warren/Karen/Jarvis...' - must be low)")
    print(f"  false positives  soft  {fp_soft:.3f}   (ordinary sentences)")
    print(f"  false positives  noise {fp_noise:.3f}")

    # ambient mic: 20 s from the headset must not trigger
    try:
        import sounddevice as sd
        dev = voice.pick_devices().get("input")
        rec = sd.rec(SR * 20, samplerate=SR, channels=1, dtype="float32", device=dev); sd.wait()
        rec = rec.ravel()
        hits = 0; n = 0
        for st in range(0, len(rec) - SR * 2, SR // 2):
            w = to_window(af, rec[st:st + SR * 2], "end")
            with torch.no_grad():
                hits += int(net(torch.tensor(w[None])).item() >= 0.5); n += 1
        print(f"  ambient headset (20 s) {hits}/{n} windows fired   (should be 0)")
    except Exception as e:                                               # noqa: BLE001
        print(f"  ambient check skipped: {type(e).__name__}")

    # export exactly the shape openWakeWord loads: [1, 16, 96] -> [1, 1]
    out = OUT / "hey_vajren.onnx"
    # Legacy (TorchScript) exporter: a plain static graph, the same shape as
    # openWakeWord's own custom models. The dynamo exporter needs onnxscript
    # and can rename/reshape inputs; not worth the variance for a 3-layer MLP.
    try:
        torch.onnx.export(net, torch.zeros(1, WIN, 96), str(out), input_names=["input"],
                          output_names=["output"], opset_version=13, dynamo=False)
    except TypeError:                                                   # older torch: no dynamo kwarg
        torch.onnx.export(net, torch.zeros(1, WIN, 96), str(out), input_names=["input"],
                          output_names=["output"], opset_version=13)
    print(f"\n  wrote {out}  ({out.stat().st_size // 1024} KB)")
    from openwakeword.model import Model
    m = Model(wakeword_models=[str(out)], inference_framework="onnx")
    print(f"  openWakeWord loads it as: {list(m.models.keys())}")
    print(f"  total {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
