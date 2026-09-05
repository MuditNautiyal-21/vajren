"""
Voice I/O — Kokoro TTS out, faster-whisper STT in. Entirely local.

WHY LOCAL, restated because it is the whole point: a voice assistant that sends
audio to a cloud API has a microphone in Mudit's room streaming to somebody
else's server. Kokoro (82M, Apache-2.0) and Whisper both run on the CPU here
while the GPU holds the model that thinks. No audio leaves the machine, ever.

DESIGN RULE: this module never raises at import and never takes the REPL down.
Every entry point degrades to text. A missing model file, an unplugged headset,
a driver that disappears mid-session — all of them mean "type instead", not a
traceback. `available()` says what actually works right now.
"""
from __future__ import annotations

import json
import os
import queue
import time
from pathlib import Path

import numpy as np

# ⚠ Must be set before huggingface_hub loads. Xet stalls at 0 bytes here and
#   never times out; see scripts/16-get-voice-models.py.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "models" / "voice"
DEVICE_FILE = ROOT / "config" / "audio-devices.json"

SR_TTS = 24_000          # Kokoro's native rate
SR_STT = 16_000          # what Whisper wants
_state: dict = {"tts": None, "stt": None, "why": {}}


# ------------------------------------------------------------------ devices --
def _sd():
    import sounddevice as sd
    return sd


def pick_devices(prefer: str = "") -> dict:
    """
    Choose a mic and an output. Saved to config/audio-devices.json so the choice
    survives a restart and can be corrected by hand.

    Prefers a headset: it is the one device where the microphone cannot hear the
    speaker. Playing TTS through speakers next to an open mic means Vajren
    transcribes itself, answers itself, and does that until someone stops it.
    """
    if DEVICE_FILE.exists():
        try:
            return json.loads(DEVICE_FILE.read_text())
        except Exception:                                          # noqa: BLE001
            pass

    sd = _sd()
    devs = sd.query_devices()
    want = (prefer or "headset headphone hyperx jabra logitech").lower().split()

    def best(is_input: bool) -> int | None:
        chans = "max_input_channels" if is_input else "max_output_channels"
        cands = [(i, d) for i, d in enumerate(devs) if d[chans] > 0
                 and "MME" in sd.query_hostapis(d["hostapi"])["name"]]
        for token in want:
            for i, d in cands:
                if token in d["name"].lower():
                    return i
        return cands[0][0] if cands else None

    chosen = {"input": best(True), "output": best(False)}
    chosen["input_name"] = devs[chosen["input"]]["name"] if chosen["input"] is not None else None
    chosen["output_name"] = devs[chosen["output"]]["name"] if chosen["output"] is not None else None
    DEVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_FILE.write_text(json.dumps(chosen, indent=2))
    return chosen


# ---------------------------------------------------------------- loading ---
def _load_tts():
    if _state["tts"] is not None:
        return _state["tts"]
    model, voices = VOICE_DIR / "kokoro-v1.0.onnx", VOICE_DIR / "voices-v1.0.bin"
    if not (model.exists() and voices.exists()):
        _state["why"]["tts"] = "models missing - run scripts/16-get-voice-models.py"
        return None
    try:
        from kokoro_onnx import Kokoro
        _state["tts"] = Kokoro(str(model), str(voices))
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["tts"] = f"{type(e).__name__}: {e}"
        _state["tts"] = None
    return _state["tts"]


def _load_stt():
    if _state["stt"] is not None:
        return _state["stt"]
    # A local directory, not a model name: naming it would send faster-whisper
    # to huggingface_hub, which stalls at 0 bytes here. The weights are fetched
    # by scripts/16-get-voice-models.py with our own downloader.
    local = VOICE_DIR / "whisper" / "small.en"
    if not (local / "model.bin").exists():
        _state["why"]["stt"] = "weights missing - run scripts/16-get-voice-models.py"
        return None
    try:
        from faster_whisper import WhisperModel
        _state["stt"] = WhisperModel(str(local), device="cpu", compute_type="int8")
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["stt"] = f"{type(e).__name__}: {e}"
        _state["stt"] = None
    return _state["stt"]


def available() -> dict:
    """{'tts': bool, 'stt': bool, 'audio': bool, 'why': {...}} — never raises."""
    out = {"tts": _load_tts() is not None, "stt": _load_stt() is not None, "audio": False,
           "why": _state["why"]}
    try:
        d = pick_devices()
        out["audio"] = d.get("input") is not None and d.get("output") is not None
        out["devices"] = d
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["audio"] = f"{type(e).__name__}: {e}"
    return out


# -------------------------------------------------------------------- TTS ---
def synth(text: str, voice: str = "af_heart", speed: float = 1.05) -> tuple | None:
    """Text -> (float32 samples, sample_rate). None if TTS is unavailable."""
    k = _load_tts()
    if k is None or not text.strip():
        return None
    try:
        samples, sr = k.create(text, voice=voice, speed=speed, lang="en-us")
        return np.asarray(samples, dtype=np.float32), sr
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["tts"] = f"{type(e).__name__}: {e}"
        return None


def to_wav(text: str, path: Path, **kw) -> bool:
    """Render speech to a WAV file. This is how the voice stack is tested
    without a human in the room — see scripts/17-voice-roundtrip.py."""
    out = synth(text, **kw)
    if out is None:
        return False
    import soundfile as sf
    samples, sr = out
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sr)
    return True


def say(text: str, **kw) -> bool:
    """Speak out loud. Returns False if it could not — caller prints instead."""
    out = synth(text, **kw)
    if out is None:
        return False
    try:
        sd = _sd()
        samples, sr = out
        sd.play(samples, sr, device=pick_devices().get("output"))
        sd.wait()
        return True
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["audio"] = f"{type(e).__name__}: {e}"
        return False


# -------------------------------------------------------------------- STT ---
def transcribe(audio, sr: int = SR_STT) -> str:
    """Audio -> text. See transcribe_scored for the confidence."""
    return transcribe_scored(audio, sr)[0]


def transcribe_scored(audio, sr: int = SR_STT) -> tuple[str, float]:
    """
    Audio (float32 array or a path) -> (text, confidence 0..1). ("", 0) on failure.

    ⚠ Confidence is ACOUSTIC, from Whisper itself — not audio length.
    The first version scored by length (0.55 + seconds/6) and a spoken "yes go
    ahead" is about one second, so every real approval landed at ~0.72, under
    the 0.75 gate, and became 'unclear' → cancel. Perfectly transcribed and
    still refused. Short utterances are the whole confirmation vocabulary; a
    score that penalises brevity penalises exactly the words that matter.

    Whisper gives avg_logprob per segment (mean log-prob of the tokens) and
    no_speech_prob. exp(avg_logprob) is the geometric-mean token probability -
    ~0.9 for clean speech, ~0.5 for mumble - and no_speech_prob catches the
    case where it invented words out of room tone.

    vad_filter drops silence before decoding, which is most of what a
    push-to-talk recording contains and would otherwise become hallucinated
    words. Whisper inventing "Thank you." out of two seconds of room tone is a
    well-known failure and it would read as a request.
    """
    m = _load_stt()
    if m is None:
        return "", 0.0
    try:
        if isinstance(audio, (str, Path)):
            source = str(audio)
        else:
            source = np.asarray(audio, dtype=np.float32).flatten()
            if sr != SR_STT:
                n = int(len(source) * SR_STT / sr)
                source = np.interp(np.linspace(0, len(source), n, endpoint=False),
                                   np.arange(len(source)), source).astype(np.float32)
        segs = list(m.transcribe(source, language="en", vad_filter=True,
                                 beam_size=1, condition_on_previous_text=False)[0])
        text = " ".join(s.text.strip() for s in segs).strip()
        if not text:
            return "", 0.0
        import math
        lp = sum(s.avg_logprob for s in segs) / len(segs)
        nsp = max(s.no_speech_prob for s in segs)
        conf = max(0.0, min(1.0, math.exp(lp) * (1.0 - nsp)))
        return text, round(conf, 3)
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["stt"] = f"{type(e).__name__}: {e}"
        return "", 0.0


def record(max_seconds: float = 20.0, silence_seconds: float = 1.2,
           start_timeout: float = 8.0) -> np.ndarray | None:
    """
    Record until the speaker stops, using an energy gate.

    Deliberately NOT a neural VAD. Silero would be better at distinguishing
    speech from noise, but it costs a torch dependency (~500 MB) to decide when
    someone stopped talking into a headset mic held six inches from their mouth.
    RMS against a noise floor measured at startup does that. Revisit if the mic
    ever moves across the room.

    Returns float32 mono at SR_STT, or None if nothing was said.
    """
    try:
        sd = _sd()
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["audio"] = f"{type(e).__name__}: {e}"
        return None

    block = 1024
    q: queue.Queue = queue.Queue()

    def cb(indata, frames, t, status):                             # noqa: ANN001
        q.put(indata.copy())

    chunks: list[np.ndarray] = []
    try:
        with sd.InputStream(samplerate=SR_STT, channels=1, dtype="float32",
                            blocksize=block, device=pick_devices().get("input"),
                            callback=cb):
            # Measure the room for a moment so the gate adapts to this mic and
            # this fan noise rather than a constant someone guessed.
            floor, t0 = [], time.time()
            while time.time() - t0 < 0.3:
                try:
                    floor.append(float(np.sqrt(np.mean(q.get(timeout=0.5) ** 2))))
                except queue.Empty:
                    break
            noise = float(np.median(floor)) if floor else 0.002
            gate = max(noise * 3.5, 0.006)

            speaking, quiet_for, started = False, 0.0, time.time()
            while True:
                try:
                    data = q.get(timeout=0.5)
                except queue.Empty:
                    if not speaking and time.time() - started > start_timeout:
                        return None
                    continue
                rms = float(np.sqrt(np.mean(data ** 2)))
                if rms > gate:
                    speaking, quiet_for = True, 0.0
                    chunks.append(data)
                elif speaking:
                    quiet_for += block / SR_STT
                    chunks.append(data)          # keep the tail; words trail off
                    if quiet_for >= silence_seconds:
                        break
                elif time.time() - started > start_timeout:
                    return None
                if time.time() - started > max_seconds:
                    break
    except Exception as e:                                         # noqa: BLE001
        _state["why"]["audio"] = f"{type(e).__name__}: {e}"
        return None

    if not chunks:
        return None
    return np.concatenate(chunks).flatten().astype(np.float32)


def listen_once(**kw) -> tuple[str, float]:
    """Record then transcribe. Returns (text, confidence 0..1)."""
    audio = record(**kw)
    if audio is None or len(audio) < SR_STT * 0.25:
        return "", 0.0
    return transcribe_scored(audio)
