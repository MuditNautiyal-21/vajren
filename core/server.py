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
REQUEST_MIN_CONF = 0.40
UTTER = ROOT / "logs" / "utterances"


def _save_utterance(audio: "np.ndarray") -> str:
    try:
        import soundfile as sf
        UTTER.mkdir(parents=True, exist_ok=True)
        path = UTTER / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.wav"
        sf.write(str(path), audio, voice.SR_STT, subtype="PCM_16")
        old = sorted(UTTER.glob("*.wav"))[:-300]
        for p in old:
            p.unlink(missing_ok=True)
        return str(path)
    except Exception:                                              # noqa: BLE001
        return ""
import re
import re as _re
_REVOKE = _re.compile(r"\bask (me )?(about|for) (that|it|everything|permission|approval)s? again\b"
                      r"|\bstop (auto[- ]?)?(approving|skipping)\b|\balways ask( me)?\b", _re.I)

UI = ROOT / "ui" / "index.html"
SESSIONS = ROOT / "logs" / "voice-sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Vajren")


@app.on_event("shutdown")
def _close_browser() -> None:
    # Vajren's Chrome is a child of this process in spirit but not in fact —
    # Playwright's launcher outlives us unless told otherwise, and an orphaned
    # browser with a logged-in profile sitting open is not acceptable.
    from core import browser
    browser.close()


# ----------------------------------------------------------------- session --
class Session:
    """Everything one conversation needs, plus the log Claude reads."""

    def __init__(self) -> None:
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = SESSIONS / f"{self.id}.jsonl"
        self.state = "idle"
        # ⚠ The thread from last time, so "do that again" and "the file you
        #   made yesterday" resolve after a restart. Mudit: "it cannot connect
        #   the next message to the last." Loaded once; new turns append.
        try:
            from core import memory
            memory.prune()
            self.conversation: list[dict] = [
                {"when": t["at"][:16], "request": t["request"], "outcome": t["outcome"],
                 "did": t["tools"] or "nothing"} for t in memory.recent_turns()]
        except Exception:                                          # noqa: BLE001
            self.conversation = []
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


def _plain(tool: str, args: dict, obs: dict, verified: bool) -> str:
    """One sentence a person can read instead of `app_click(window=..., ref=2)`.

    The transcript is the one place Mudit sees WHAT it did. Tool names and
    argument dumps are for the log; this is for him.
    """
    a = args or {}
    leaf = lambda p: str(p).replace("\\", "/").rstrip("/").split("/")[-1]
    host = lambda u: re.sub(r"^https?://(www\.)?", "", str(u)).split("/")[0]
    err = obs.get("error")
    if err and "already did" in str(err):
        return "tried to repeat a step already done"
    if not verified and err:
        return f"couldn't {tool.replace('_', ' ')}: {str(err)[:80]}"
    t = tool
    if t == "open_app":       return f"opened {a.get('app', '')}" + (f" on {leaf(a['path'])}" if a.get("path") else "")
    if t == "open_url":       return f"opened {host(a.get('url'))} in your browser" + (f" ({a['profile']} profile)" if a.get("profile") else "")
    if t == "focus_window":   return f"brought {a.get('title', '')!s} to the front"
    if t == "close_window":   return f"closed {a.get('title', '')!s}" + (" (forced)" if a.get("force") else "")
    if t == "search_files":   return f"looked for {a.get('pattern', '')} — {obs.get('count', 0)} found"
    if t == "read_file":      return f"read {leaf(a.get('path', ''))}"
    if t == "write_file":     return f"wrote {leaf(a.get('path', ''))}"
    if t == "trash_file":     return f"moved {leaf(a.get('path', ''))} to the trash"
    if t == "undo_file":      return "undid the last file change"
    if t == "list_directory": return f"listed {leaf(a.get('path', ''))}"
    if t == "run_shell":      return f"ran: {str(a.get('command', ''))[:60]}"
    if t == "browser_open":   return f"opened {host(a.get('url'))} in my browser"
    if t == "browser_read":   return "read the page"
    if t == "browser_find":   return f"looked for “{a.get('query', '')}” on the page" if a.get("query") else "looked at what's on the page"
    if t == "browser_click":  return f"clicked “{a.get('label', '')}”"
    if t == "browser_type":   return f"typed “{str(a.get('text', ''))[:40]}” into {a.get('label', '')}" + (" and pressed enter" if a.get("submit") else "")
    if t == "browser_back":   return "went back a page"
    if t == "app_find":       return f"looked for “{a.get('query', '')}” in {a.get('window', '')}" if a.get("query") else f"looked at {a.get('window', '')}"
    if t == "app_click":      return f"clicked “{a.get('label', '')}” in {a.get('window', '')}"
    if t == "app_type":       return f"typed “{str(a.get('text', ''))[:40]}” into {a.get('label', '')}" + (" and pressed enter" if a.get("submit") else "")
    if t == "look_at_screen": return "looked at the screen"
    if t == "remember_fact":  return f"remembered: {a.get('fact', '')}"
    if t == "recall":         return f"checked memory for “{a.get('query', '')}”"
    if t == "forget_fact":    return f"forgot: {a.get('about', '')}"
    return t.replace("_", " ")


async def send_context(ws: WebSocket, request: str = "") -> None:
    """The left column: what it sees, what it remembers, what it has earned."""
    try:
        from core import desktop, memory
        wins = desktop.windows(12)
        cur = None
        try:
            from core import browser
            cur = browser.current()
        except Exception:                                          # noqa: BLE001
            pass
        facts = memory.recall(request) if request else []
        st = memory.stats()
        trust = [t for t in memory.trust_report() if not t.get("revoked_at")][:6]
        await send(ws, type="context", windows=wins, own_browser=cur,
                   facts=[{"fact": f["fact"], "source": f["source"]} for f in facts[:5]],
                   fact_total=st.get("facts", 0), trust=trust)
    except Exception as e:                                         # noqa: BLE001
        SESSION.log("context_error", error=str(e))


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
    await send_context(ws, request)

    # "Ask me about that again" is a control over the gate, not a task for the
    # planner, so it is decided here, in code, before any model sees it.
    if _REVOKE.search(request):
        from core import memory
        n = memory.revoke().get("revoked", 0)
        msg = ("Okay — I'll ask about everything again." if n
               else "I wasn't skipping any approvals, but okay.")
        SESSION.log("trust_revoked", count=n)
        await send(ws, type="done", summary=msg, elapsed=0)
        await say(ws, msg)
        await set_state(ws, "idle")
        return
    t0 = time.perf_counter()
    await set_state(ws, "thinking")

    state = await asyncio.to_thread(
        graph.invoke, {"request": request, "sources": {"local_files"},
                       "conversation": SESSION.conversation,
                       "session_id": SESSION.id}, SESSION.cfg)
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
                   said=_plain(step["tool"], step["args"], obs, step["verified"]),
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
    request_text = SESSION.log_last_request()
    tools = [h["tool"] for h in state.get("history", []) if h.get("verified")]
    SESSION.conversation.append({"request": request_text, "outcome": answer, "did": " ".join(tools) or "nothing"})
    SESSION.conversation = SESSION.conversation[-12:]
    status = "cancelled" if cancelled else ("completed" if state.get("proposed", {}).get("done") else "failed")
    try:
        from core import memory
        memory.record_turn(SESSION.id, ep, request_text, answer, tools, status)
        if status == "completed" and tools:
            memory.distill_later(request_text, answer, tools)       # off the hot path
    except Exception as e:                                          # noqa: BLE001
        SESSION.log("memory_error", error=str(e))
    if state.get("earned_trust"):
        # Said once, out loud, at the moment it happens.
        answer += (f" By the way — that's the third time you've said yes to {state['earned_trust']}, "
                   f"so I'll stop asking about that one. Say 'ask me about that again' to undo it.")
        SESSION.log("trust_granted", shape=state["earned_trust"])
    SESSION.log("done", summary=answer, elapsed=elapsed, episode=ep)
    await send(ws, type="done", summary=answer, elapsed=elapsed)
    await send_context(ws)
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
    # ⚠ Keep the audio. "It's hearing everything wrong — check the logs!" and
    #   the logs had the words it decided on and nothing it decided them FROM.
    #   Every utterance is now a WAV in logs/utterances/, named by time, last
    #   300 kept, so the next complaint can be replayed through Whisper with
    #   different settings instead of guessed at. Also its loudness: a quiet
    #   mic reads as garbage and looks identical to a bad model in the text.
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    clip = _save_utterance(audio)
    t0 = time.perf_counter()
    text, conf = await asyncio.to_thread(voice.transcribe_scored, audio)
    took = round(time.perf_counter() - t0, 2)
    SESSION.log("heard", text=text, conf=conf, audio_seconds=round(seconds, 2), stt_seconds=took,
                rms=round(rms, 4), peak=round(peak, 3), wav=clip)
    # A request it barely made out is not a request to act on. The gate had a
    # confidence floor since J-030; ordinary requests had NONE — "I have a
    # bunch of land" at 0.36 went straight to the planner. Measured floor
    # (17b): clean speech 0.44+, garbage 0.19-0.32. Approvals keep their own.
    if text and conf < REQUEST_MIN_CONF and not SESSION.pending_gate:
        SESSION.log("heard_unclear", text=text, conf=conf)
        await send(ws, type="heard", text=text, conf=conf, verdict="unclear")
        await say(ws, "Sorry, I didn't catch that — say it again?")
        await set_state(ws, "idle")
        return
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
    # ⚠ no-store, or WebView2 keeps serving the face it cached last week.
    #   After the v2 face shipped, the window came up showing v1 from cache;
    #   the server was right and the screen was wrong.
    return FileResponse(UI, media_type="text/html",
                        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


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
               cancel=POLICY.confirmation["cancel_phrases"],
               # the last few turns of the previous session, so the screen shows
               # the thread continuing rather than a blank slate
               earlier=[{"when": c.get("when", ""), "request": c["request"], "outcome": c["outcome"]}
                        for c in SESSION.conversation[-3:] if c.get("when")])
    await set_state(ws, "idle")
    await send_context(ws)
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
