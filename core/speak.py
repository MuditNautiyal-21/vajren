"""
Voice output, with a zero-dependency floor.

There is a chicken-and-egg problem in first run: Vajren should speak its name
before anything has been installed, but the good TTS engine is one of the things
that has to be installed. So this has two levels.

  level 0  OS-native speech. Windows SAPI via PowerShell, macOS `say`,
           Linux espeak/spd-say. Present on a stock machine, no install, no
           network. Robotic, and that is fine for eight words on first boot.

  level 1  Kokoro-82M via ONNX. Natural, streams, ~300 ms to first audio.
           Installed during setup, used from then on.

Everything else in VAJREN calls speak(). It never needs to know which level is
available, and the assistant is never mute.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "config" / "identity.json"

_SYSTEM = platform.system()


# --------------------------------------------------------------------------- #
#  Level 0 — always available
# --------------------------------------------------------------------------- #
def _say_windows(text: str) -> bool:
    # SAPI has shipped with every Windows since XP. No install, no network.
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 0; "
        f"$s.Speak([Console]::In.ReadToEnd())"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       input=text, text=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _say_macos(text: str) -> bool:
    try:
        subprocess.run(["say", text], timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _say_linux(text: str) -> bool:
    for cmd in (["spd-say", "-w", text], ["espeak", text], ["espeak-ng", text]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, timeout=60,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                continue
    return False


def _native(text: str) -> bool:
    return {"Windows": _say_windows,
            "Darwin": _say_macos}.get(_SYSTEM, _say_linux)(text)


# --------------------------------------------------------------------------- #
#  Level 1 — Kokoro, once setup has installed it
# --------------------------------------------------------------------------- #
def _kokoro(text: str) -> bool:
    try:
        from voice.tts import speak_stream  # built in Phase 03
    except Exception:
        return False
    try:
        speak_stream(text)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Public
# --------------------------------------------------------------------------- #
def speak(text: str, *, also_print: bool = True) -> None:
    """Say it out loud if we can, print it either way. Never raises, never blocks
    the caller on a missing voice stack."""
    if also_print:
        print(f"\n  Vajren: {text}\n")
    if not _kokoro(text):
        _native(text)


def available_level() -> int:
    try:
        import voice.tts  # noqa: F401
        return 1
    except Exception:
        pass
    if _SYSTEM == "Windows" or (_SYSTEM == "Darwin" and shutil.which("say")):
        return 0
    return 0 if any(shutil.which(c) for c in ("spd-say", "espeak", "espeak-ng")) else -1


def boss() -> str:
    """What to call the person. Falls back to nothing rather than guessing."""
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("boss", "")
    except Exception:
        return ""
