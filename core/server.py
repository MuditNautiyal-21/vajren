"""
The face's back end. One websocket, one session, one person.

    .venv\\Scripts\\python.exe -m core.server        ->  http://127.0.0.1:7777

WHY A BROWSER AND NOT A WINDOW: the browser already has the microphone, the
speaker, a GPU-accelerated canvas, and permission prompts that people trust.
Everything Vajren does still happens in this process — the page is a mouth, an
ear and a face, nothing more. It talks to nobody but 127.0.0.1.

WHY THIS MAKES TESTING POSSIBLE: every utterance, transcription, confidence,
verdict, step and timing is appended to logs/voice-sessions/<stamp>.jsonl as it
happens. Claude cannot hear the microphone; it can read that file. That is how
"you talk, I monitor" works.

PROTOCOL (websocket at /ws)
  client -> server
    binary          one utterance: float32 little-endian PCM, 16 kHz, mono
    {"type":"text","text":...}         typed instead of spoken
    {"type":"approve"} / {"type":"cancel"}   buttons, when the gate is open
  server -> client
    {"type":"state","state":...}       idle|listening|transcribing|thinking|
                                        awaiting_approval|speaking|error
    {"type":"heard","text","conf","verdict"?}
    {"type":"say","text"}  then  binary WAV of that text
    {"type":"step","tool","args","verified","error"?}
    {"type":"ask","speak","show","tool","reversible"}
                                       the gate is open; next utterance decides.
                                       `show` is the EXACT argument and is
                                       displayed verbatim; `speak` may be an
                                       abbreviation of it, for the ear only.
    {"type":"done","summary","elapsed"}
    {"type":"hello","session","voice":{...}}
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import confirm, voice                                 # noqa: E402
from core.policy import POLICY                                  # noqa: E402

FACE_PORT = int(os.environ.get("VAJREN_FACE_PORT", "7777"))

UI = ROOT / "ui" / "index.html"
SESSIONS = ROOT / "logs" / "voice-sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Vajren")


# ----------------------------------------------------------------- session --
class Session:
    """Everything one conversation needs, plus the log Claude reads."""

    def __init__(self) -> None:
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = SESSIONS / f"{self.id}.jsonl"
        self.state = "idle"
        self.conversation: list[dict] = []
        self.graph = None
        self.cfg: dict | None = None
        self.pending_gate: dict | None = None     # the interrupt payload, if open
        self.lock = asyncio.Lock()
        self.played = asyncio.Event()             # page finished playing the last WAV
        self.turns = 0
        self.log("session_start", voice=voice.available())

    def log(self, kind: str, **fields) -> None:
        rec = {"t": datetime.now().isoformat(timespec="milliseconds"), "type": kind, **fields}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def app_graph(self):
        if self.graph is None:
            from core.graph import build
            self.graph = build()
        return self.graph


SESSION = Session()


# ------------------------------------------------------------------- audio --
def wav_bytes(text: str) -> bytes | None:
    out = voice.synth(text)
    if out is None:
        return None
    import soundfile as sf
    samples, sr = out
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def send(ws: WebSocket, **msg) -> None:
    await ws.send_text(json.dumps(msg, default=str))


async def set_state(ws: WebSocket, state: str) -> None:
    SESSION.state = state
    SESSION.log("state", state=state)
    await send(ws, type="state", state=state)


async def say(ws: WebSocket, text: str, *, show_text: bool = True) -> None:
    """Text first (so the transcript is instant), then the audio, then wait for
    the client to report playback finished so the mic does not reopen while
    Vajren is still talking — the headset makes feedback unlikely, the state
    machine makes it impossible."""
    await set_state(ws, "speaking")
    # The approval card already prints this sentence, with the exact argument
    # under it. Printing it twice makes the transcript look like a stutter and
    # pushes the literal — the part that matters — off screen.
    if show_text:
        await send(ws, type="say", text=text)
    SESSION.log("say", text=text)
    t0 = time.perf_counter()
    audio = await asyncio.to_thread(wav_bytes, text)
    if audio:
        SESSION.played.clear()
        await ws.send_bytes(audio)
        SESSION.log("tts", chars=len(text), bytes=len(audio), seconds=round(time.perf_counter() - t0, 2))
        # ~44 bytes header, 2 bytes/sample at 24 kHz. Wait for the page to say
        # it finished, with a ceiling so a muted tab cannot hang the session.
        est = (len(audio) - 44) / 2 / voice.SR_TTS
        try:
            await asyncio.wait_for(SESSION.played.wait(), timeout=est + 3.0)
        except asyncio.TimeoutError:
            SESSION.log("playback_timeout", seconds=round(est, 1))


# -------------------------------------------------------------------- loop --
async def run_request(ws: WebSocket, request: str) -> None:
    """Drive the graph for one request, streaming steps and gates to the page."""
    from langgraph.types import Command

    graph = SESSION.app_graph()
    SESSION.cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    SESSION.turns += 1
    SESSION.log("request", text=request, turn=SESSION.turns)
    t0 = time.perf_counter()
    await set_state(ws, "thinking")

    state = await asyncio.to_thread(
        graph.invoke, {"request": request, "sources": {"local_files"},
                       "conversation": SESSION.conversation}, SESSION.cfg)
    await _after_invoke(ws, state, t0, shown=0)


async def resume(ws: WebSocket, verdict: str) -> None:
    from langgraph.types import Command
    gate = SESSION.pending_gate or {}
    SESSION.pending_gate = None
    SESSION.log("verdict", verdict=verdict, tool=gate.get("tool"))
    t0 = gate.get("t0", time.perf_counter())
    await set_state(ws, "thinking")
    state = await asyncio.to_thread(
        SESSION.app_graph().invoke,
        Command(resume="approve" if verdict == "approve" else "cancel"), SESSION.cfg)
    await _after_invoke(ws, state, t0, shown=gate.get("shown", 0), cancelled=(verdict != "approve"))


async def _after_invoke(ws: WebSocket, state: dict, t0: float, shown: int,
                        cancelled: bool = False) -> None:
    hist = state.get("history", [])
    for step in hist[shown:]:
        obs = step["observation"]
        SESSION.log("step", tool=step["tool"], args=step["args"], verified=step["verified"],
                    error=obs.get("error"), injection=obs.get("INJECTION_ATTEMPT_IN_DATA"))
        await send(ws, type="step", tool=step["tool"], args=step["args"],
                   verified=step["verified"], error=obs.get("error"),
                   injection=bool(obs.get("INJECTION_ATTEMPT_IN_DATA")))

    if "__interrupt__" in state:
        payload = state["__interrupt__"][0].value
        SESSION.pending_gate = {**payload, "t0": t0, "shown": len(hist)}
        SESSION.log("gate", speak=payload["speak"], show=payload.get("show", ""),
                    tool=payload.get("tool"))
        await send(ws, type="ask", speak=payload["speak"], show=payload.get("show", ""),
                   tool=payload.get("tool"), reversible=payload.get("reversible"))
        await say(ws, payload["speak"], show_text=False)
        await set_state(ws, "awaiting_approval")
        return

    elapsed = round(time.perf_counter() - t0, 1)
    # After a cancel, `proposed` still holds the plan that was refused. Reading
    # its summary here made Vajren announce the cancelled action as if it had
    # happened — "I'll update the file to contain nope" — right after refusing
    # to. What it did is nothing, and it should say so.
    answer = ("Cancelled. Nothing was done." if cancelled
              else state.get("proposed", {}).get("spoken_summary") or "Cancelled. Nothing was done.")
    ep = state.get("episode_id")
    if ep:
        from core.tools import close_episode
        done = bool(state.get("proposed", {}).get("done"))
        err = (state.get("result") or {}).get("error")
        close_episode(ep, "completed" if done else ("cancelled" if not err else "failed"), err)
    SESSION.conversation.append({"request": SESSION.log_last_request(), "outcome": answer})
    SESSION.log("done", summary=answer, elapsed=elapsed, episode=ep)
    await send(ws, type="done", summary=answer, elapsed=elapsed)
    await say(ws, answer)
    await set_state(ws, "idle")


def _last_request(self) -> str:
    try:
        for line in reversed(self.path.read_text(encoding="utf-8").splitlines()):
            rec = json.loads(line)
            if rec.get("type") == "request":
                return rec["text"]
    except Exception:                                            # noqa: BLE001
        pass
    return ""


Session.log_last_request = _last_request


# ------------------------------------------------------------------ input ---
async def handle_utterance(ws: WebSocket, pcm: bytes) -> None:
    audio = np.frombuffer(pcm, dtype=np.float32)
    seconds = len(audio) / voice.SR_STT
    if seconds < 0.3:
        SESSION.log("utterance_too_short", seconds=round(seconds, 2))
        return
    await set_state(ws, "transcribing")
    t0 = time.perf_counter()
    text, conf = await asyncio.to_thread(voice.transcribe_scored, audio)
    took = round(time.perf_counter() - t0, 2)
    SESSION.log("heard", text=text, conf=conf, audio_seconds=round(seconds, 2), stt_seconds=took)
    if not text:
        await send(ws, type="heard", text="", conf=0.0)
        await set_state(ws, "awaiting_approval" if SESSION.pending_gate else "idle")
        return
    await handle_text(ws, text, conf)


async def handle_text(ws: WebSocket, text: str, conf: float) -> None:
    if SESSION.pending_gate:
        speak_txt = SESSION.pending_gate.get("speak", "")
        verdict, reply = await asyncio.to_thread(confirm.resolve, text, conf, speak_txt)
        await send(ws, type="heard", text=text, conf=conf, verdict=verdict)
        if verdict == "neither":
            # They said something that is not an answer — usually a question.
            # Answer it and ask again, rather than reciting the magic phrase.
            SESSION.log("verdict", verdict="neither", heard=text, reply=reply)
            await say(ws, reply)
            await set_state(ws, "awaiting_approval")
            return
        await resume(ws, verdict)
        return
    await send(ws, type="heard", text=text, conf=conf)
    await run_request(ws, text)


# ----------------------------------------------------------------- routes ---
@app.get("/")
async def index():
    return FileResponse(UI, media_type="text/html")


@app.get("/status")
async def status():
    """What Claude polls while Mudit talks. Plain JSON, no auth, loopback only."""
    return JSONResponse({
        "session": SESSION.id, "log": str(SESSION.path), "state": SESSION.state,
        "turns": SESSION.turns, "gate_open": SESSION.pending_gate is not None,
        "voice": voice.available(),
    })


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    SESSION.log("ws_connect")
    await send(ws, type="hello", session=SESSION.id, voice=voice.available(),
               affirm=POLICY.confirmation["affirm_phrases"],
               cancel=POLICY.confirmation["cancel_phrases"])
    await set_state(ws, "idle")
    worker: asyncio.Task | None = None
    queued: list = []          # at most ONE waiting input; the newest wins

    async def guarded(coro) -> None:
        # One request at a time — but an input that arrives while Vajren is
        # still talking is QUEUED, not dropped. The first version dropped it,
        # and the very first realistic test (answer the question the moment
        # you hear it, while the TTS is still rendering) lost the answer and
        # hung. People start replying before the sentence ends; that is not an
        # error, it is how conversation works. Only a THIRD input, arriving
        # while one is already waiting, replaces the waiting one.
        nonlocal worker
        if worker and not worker.done():
            if queued:
                SESSION.log("queued_replaced")
                queued[0].close()
                queued.clear()
            SESSION.log("queued")
            queued.append(coro)
            await send(ws, type="busy")
            return

        async def wrapped(c):
            nonlocal worker
            try:
                await c
            except WebSocketDisconnect:
                raise
            except Exception as e:                                # noqa: BLE001
                import traceback
                tb = traceback.format_exc()
                SESSION.log("error", error=f"{type(e).__name__}: {e}", traceback=tb)
                (ROOT / "logs" / "face-errors.log").open("a", encoding="utf-8").write(
                    f"\n--- {datetime.now().isoformat()}\n{tb}")
                await send(ws, type="error", text=f"{type(e).__name__}: {str(e)[:200]}")
                await set_state(ws, "idle")
            finally:
                # Start the waiting input directly — NOT via guarded(), which
                # would see this task as still running and queue it forever.
                if queued:
                    SESSION.log("dequeued")
                    worker = asyncio.create_task(wrapped(queued.pop(0)))
        worker = asyncio.create_task(wrapped(coro))

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await guarded(handle_utterance(ws, msg["bytes"]))
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind == "played":
                SESSION.played.set()
            elif kind == "text" and data.get("text", "").strip():
                await guarded(handle_text(ws, data["text"].strip(), 1.0))
            elif kind in ("approve", "cancel") and SESSION.pending_gate:
                SESSION.log("button", verdict=kind)
                await guarded(resume(ws, kind))
            elif kind == "ping":
                await send(ws, type="pong", state=SESSION.state)
    except WebSocketDisconnect:
        pass
    finally:
        SESSION.log("ws_disconnect")
        if worker and not worker.done():
            worker.cancel()


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    print(f"\n  VAJREN face  ->  http://127.0.0.1:7777\n  session log  ->  {SESSION.path}\n")
    # Loopback only. This process holds every capability Vajren has; it is not
    # something to expose to the LAN, and certainly not to a phone yet (Phase 07
    # does that through Tailscale, with auth, on purpose).
    # ⚠ Overridable so the automated face test can run its OWN server instead
    #   of borrowing whichever one happens to be listening. It borrowed Mudit's
    #   live window once and injected "Create a file at face-test.txt" into the
    #   middle of a real conversation while he was waiting at an approval
    #   prompt. A test that can only run by intruding on the user is not a test
    #   you can leave in a suite.
    uvicorn.run(app, host="127.0.0.1", port=FACE_PORT, log_level="warning")


if __name__ == "__main__":
    main()
