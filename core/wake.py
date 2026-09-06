"""
Wake word: "hey jarvis", on the CPU, always listening. Phase 03's missing half.

WHY THIS MODEL: openWakeWord ships a pretrained 'hey_jarvis' that runs at
~1.2 ms per 80 ms frame on this CPU (measured 2026-09-06) - effectively free
next to Whisper - and it is, for a J.A.R.V.I.S.-shaped assistant, the right
phrase. A custom "hey Vajren" needs a synthetic-speech training run; that is
the upgrade, and the hook is one line (the model name below).

HOW: a daemon thread opens the default input at 16 kHz mono via sounddevice
(the same library core/voice.py records with), feeds 80 ms frames to the
detector, and on a confident hit hands a one-shot `wake` to the server, which
sends it to the face. The FACE does the capture - the browser mic already has
echo cancellation and the sphere - and its wakeVad ends it on silence.

⚠ TWO GATES ON THE TRIGGER, or it hears itself:
  1. Muted unless the session is idle or waiting on the gate. Vajren speaking
     "…shall I?" through the headset would otherwise wake itself, every time.
  2. A 3 s cooldown after any hit, and a score threshold of 0.5 with two
     consecutive frames required. openWakeWord's own false-positive rate is
     low; the second frame makes a passing "Jarvis" in a podcast not count.

Never runs if the model or the mic is unavailable: it logs once and exits.
Push-to-talk is unaffected either way.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from pathlib import Path

# ⚠ "hey Vajren" is a CUSTOM model, trained locally by scripts/43-train-wake.py
#   from Kokoro-spoken samples (no downloads, no cloud). If the file exists it
#   is the wake word; if not, the pretrained "hey jarvis" is. Mudit, 2026-09-06:
#   "its not activating through hey vajren" - the assistant is called Vajren;
#   it should answer to its own name.
CUSTOM = Path(__file__).resolve().parents[1] / "models" / "wake" / "hey_vajren.onnx"
MODEL = str(CUSTOM) if CUSTOM.exists() else "hey_jarvis"
PHRASE = "vajren" if CUSTOM.exists() else "hey jarvis"   # the bare name, since J-055
RATE = 16000
FRAME = 1280                     # 80 ms at 16 kHz - what openWakeWord expects
# The custom model measured 99.7% true-positive at 0.5 with 4.2% single-frame
# false hits on phonetic neighbours ("hey Warren"). Scores are well separated,
# so a higher bar costs almost nothing in recall and buys quiet.
THRESHOLD = 0.6 if CUSTOM.exists() else 0.5
COOLDOWN_S = 3.0


def start(on_wake: Callable[[], None], is_listenable: Callable[[], bool],
          log: Callable[[str], None] = print) -> threading.Thread | None:
    """Begin listening. Returns the thread, or None if wake is unavailable."""
    try:
        import numpy as np
        import sounddevice as sd
        import openwakeword
        from openwakeword.model import Model
        if not CUSTOM.exists():
            try:
                openwakeword.utils.download_models(model_names=[MODEL])
            except Exception:                                      # noqa: BLE001
                pass                                               # already present, or offline
        model = Model(wakeword_models=[MODEL], inference_framework="onnx")
        # openWakeWord keys predictions by the model's file stem, whether it
        # came from a name ("hey_jarvis") or a path (".../hey_vajren.onnx").
        key = Path(MODEL).stem
    except Exception as e:                                         # noqa: BLE001
        log(f"wake: unavailable ({type(e).__name__}: {str(e)[:80]}) - push-to-talk only")
        return None

    # ⚠ NOT the default device. Measured 2026-09-06: sounddevice's default
    #   input on this box is "Microphone (USB Camera MIC)" - the webcam - while
    #   Mudit speaks into the HyperX headset. The first version listened for
    #   "hey jarvis" on a camera across the room and never heard a word. Use
    #   the SAME pick as Whisper and the face (core.voice.pick_devices), which
    #   prefers the headset by name. Fall back to default only if that fails.
    device = None
    try:
        from core.voice import pick_devices
        device = pick_devices().get("input")
    except Exception:                                              # noqa: BLE001
        pass

    def run() -> None:
        last_hit = 0.0
        hits = 0
        try:
            name = sd.query_devices(device)["name"] if device is not None else "default input"
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                device=device) as stream:
                log(f"wake: listening for '{PHRASE}' on {name}")
                while True:
                    frame, _ = stream.read(FRAME)
                    if not is_listenable():
                        model.reset()                              # do not accumulate our own voice
                        hits = 0
                        continue
                    score = float(model.predict(np.frombuffer(frame, dtype=np.int16)).get(key, 0.0))
                    if score >= THRESHOLD:
                        hits += 1
                    else:
                        hits = 0
                    if hits >= 2 and time.time() - last_hit > COOLDOWN_S:
                        last_hit = time.time()
                        hits = 0
                        model.reset()
                        log(f"wake: heard it ({score:.2f})")
                        try:
                            on_wake()
                        except Exception:                          # noqa: BLE001
                            pass
        except Exception as e:                                     # noqa: BLE001
            log(f"wake: stopped ({type(e).__name__}: {str(e)[:80]})")

    t = threading.Thread(target=run, daemon=True, name="vajren-wake")
    t.start()
    return t
