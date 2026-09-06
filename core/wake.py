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

MODEL = "hey_jarvis"
RATE = 16000
FRAME = 1280                     # 80 ms at 16 kHz - what openWakeWord expects
THRESHOLD = 0.5
COOLDOWN_S = 3.0


def start(on_wake: Callable[[], None], is_listenable: Callable[[], bool],
          log: Callable[[str], None] = print) -> threading.Thread | None:
    """Begin listening. Returns the thread, or None if wake is unavailable."""
    try:
        import numpy as np
        import sounddevice as sd
        import openwakeword
        from openwakeword.model import Model
        try:
            openwakeword.utils.download_models(model_names=[MODEL])
        except Exception:                                          # noqa: BLE001
            pass                                                   # already present, or offline
        model = Model(wakeword_models=[MODEL], inference_framework="onnx")
    except Exception as e:                                         # noqa: BLE001
        log(f"wake: unavailable ({type(e).__name__}: {str(e)[:80]}) - push-to-talk only")
        return None

    def run() -> None:
        last_hit = 0.0
        hits = 0
        try:
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
                log(f"wake: listening for '{MODEL.replace('_', ' ')}'")
                while True:
                    frame, _ = stream.read(FRAME)
                    if not is_listenable():
                        model.reset()                              # do not accumulate our own voice
                        hits = 0
                        continue
                    score = float(model.predict(np.frombuffer(frame, dtype=np.int16)).get(MODEL, 0.0))
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
