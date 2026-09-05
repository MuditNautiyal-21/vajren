"""19 - Drive the face's websocket exactly as the browser would. No browser, no mic.

Sends a typed request that needs approval, receives the spoken question as WAV
bytes, then answers it with SPEECH: Kokoro synthesizes "yes go ahead", the test
resamples it to 16 kHz float32 - the exact frame the page sends from the mic -
and pushes it down the socket. Whisper transcribes it, the parser approves it,
the graph resumes, the file appears.

If this passes, the only untested part of the voice path is the physical mic.

    (server running)  .venv\\Scripts\\python.exe scripts\\19-face-test.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import voice                                          # noqa: E402

OUT = ROOT / "sandbox" / "face-test.txt"
fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


def speech_pcm16k(text: str) -> bytes:
    """What the browser sends: float32 LE mono at 16 kHz."""
    samples, sr = voice.synth(text)
    n = int(len(samples) * 16000 / sr)
    y = np.interp(np.linspace(0, len(samples), n, endpoint=False), np.arange(len(samples)), samples)
    return y.astype(np.float32).tobytes()


async def main() -> int:
    import websockets
    if OUT.exists():
        OUT.unlink()
    events: list[dict] = []
    wavs: list[int] = []

    async with websockets.connect("ws://127.0.0.1:7777/ws", max_size=None) as ws:

        async def pump(until: str, timeout: float = 240) -> dict | None:
            """Read until an event of type `until`, acking WAVs like the page does."""
            t0 = time.time()
            while time.time() - t0 < timeout:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                if isinstance(raw, (bytes, bytearray)):
                    wavs.append(len(raw))
                    await ws.send(json.dumps({"type": "played"}))   # instant "finished playing"
                    continue
                ev = json.loads(raw)
                events.append(ev)
                if ev.get("type") == until:
                    return ev
                if ev.get("type") == "error":
                    return ev
            return None

        hello = await pump("hello")
        check("hello with session id", bool(hello and hello.get("session")))
        check("server reports voice usable", bool(hello and hello["voice"]["tts"] and hello["voice"]["stt"]))

        print("\n== typed request that needs approval")
        await ws.send(json.dumps({"type": "text",
                                  "text": f"Create a file at {OUT} containing the word: face"}))
        ask = await pump("ask")
        check("gate opened with a spoken question", bool(ask and ask.get("type") == "ask"), str(ask)[:200])
        check("...that names the exact file", bool(ask) and str(OUT) in ask.get("speak", ""))
        st = await pump("state")
        while st and st.get("state") != "awaiting_approval":
            st = await pump("state")
        check("state is awaiting_approval", bool(st))
        check("the question arrived as audio (WAV bytes)", len(wavs) >= 1 and wavs[-1] > 10_000,
              f"wavs={wavs}")

        print("\n== answer it with SPEECH: Kokoro says 'yes go ahead' into the socket")
        await ws.send(speech_pcm16k("yes go ahead"))
        heard = await pump("heard")
        check("transcribed", bool(heard and heard.get("text")), str(heard))
        check("parsed as approve", bool(heard) and heard.get("verdict") == "approve", str(heard))
        done = await pump("done")
        check("request completed", bool(done and done.get("type") == "done"), str(done)[:200])
        check("file exists with the right content", OUT.exists() and "face" in OUT.read_text())
        steps = [e for e in events if e.get("type") == "step"]
        check("write_file step reported and verified",
              any(s["tool"] == "write_file" and s["verified"] for s in steps), str(steps)[:200])
        # `done` is sent BEFORE the answer is spoken (text first, so the transcript
        # is instant); the WAV follows, then state returns to idle. Wait for that.
        st = await pump("state")
        while st and st.get("state") != "idle":
            st = await pump("state")
        check("the answer was spoken too", len(wavs) >= 2, f"wavs={wavs}")

        print("\n== speech that is NOT an approval must not approve")
        if OUT.exists():
            OUT.unlink()
        await ws.send(json.dumps({"type": "text",
                                  "text": f"Create a file at {OUT} containing the word: nope"}))
        await pump("ask")
        await ws.send(speech_pcm16k("hmm, what is this file for"))
        heard = await pump("heard")
        check("unclear speech did not approve", bool(heard) and heard.get("verdict") != "approve", str(heard))
        await pump("state")
        await ws.send(json.dumps({"type": "cancel"}))            # the button, this time
        done = await pump("done")
        check("cancel button ends the request", bool(done))
        check("nothing was written", not OUT.exists())

    sess = hello["session"] if hello else "?"
    log = ROOT / "logs" / "voice-sessions" / f"{sess}.jsonl"
    n = sum(1 for _ in open(log, encoding="utf-8")) if log.exists() else 0
    check("session log exists and grew", n > 10, f"{log} lines={n}")
    print(f"\n  session log: {log}  ({n} events)")
    print(f"{'ALL PASS' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
