"""
Remote access over Telegram (Phase 07). Vajren from a phone.

    .venv\\Scripts\\python.exe -m core.remote

Needs two lines in C:\\vajren\\.env (the file is denylisted from every tool):
    TELEGRAM_BOT_TOKEN=123456:ABC...      from @BotFather
    TELEGRAM_ALLOWED_USER_ID=987654321    your numeric id, from @userinfobot

HOW: long-polls the Bot API with stdlib urllib (no SDK, nothing to update),
connects to the face's websocket as a second client, forwards each text
message as a request, and relays what comes back — the spoken reply, a gate
question ("yes"/"no" answers it), the final summary. Audio frames are dropped;
the phone gets words.

⚠ SECURITY, in order of importance:
  1. Exactly ONE Telegram user id is honoured. Everyone else is ignored
     silently — not refused, not answered, not logged by id. A bot token is a
     public address the moment it is in a URL; the allowlist is the lock.
  2. The websocket is 127.0.0.1 only. Telegram never reaches the box; the box
     reaches Telegram. No port is opened. Tailscale is not required for this
     path, which is why it ships first.
  3. Every action still goes through the gate exactly as it does from the
     desk. A remote "yes" is parsed by the same interpret_confirmation.
     Nothing gets a shortcut for arriving by phone.
  4. The token is read once from .env and never printed, logged or sent.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACE = os.environ.get("VAJREN_FACE_WS", "ws://127.0.0.1:7777/ws")


def _env() -> tuple[str, int]:
    tok, uid = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "TELEGRAM_BOT_TOKEN" and not tok:
                    tok = v
                if k == "TELEGRAM_ALLOWED_USER_ID" and not uid:
                    uid = v
    if not tok or not uid.isdigit():
        print("remote: TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID must be set in .env", file=sys.stderr)
        sys.exit(2)
    return tok, int(uid)


class Telegram:
    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}/"
        self.offset = 0

    def _call(self, method: str, **params) -> dict:
        data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(self._base + method, data=data)
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8"))

    def poll(self) -> list[dict]:
        try:
            out = self._call("getUpdates", offset=self.offset, timeout=30, allowed_updates='["message"]')
        except Exception:                                          # noqa: BLE001
            time.sleep(3)
            return []
        ups = out.get("result", []) if out.get("ok") else []
        if ups:
            self.offset = ups[-1]["update_id"] + 1
        return ups

    def send(self, chat_id: int, text: str) -> None:
        for chunk in (text[i:i + 3800] for i in range(0, max(len(text), 1), 3800)):
            try:
                self._call("sendMessage", chat_id=chat_id, text=chunk)
            except Exception:                                      # noqa: BLE001
                pass


async def bridge_one(text: str, tg: Telegram, chat_id: int) -> None:
    """Send one request to the face and relay everything until it is done."""
    import websockets
    try:
        async with websockets.connect(FACE, max_size=64 * 1024 * 1024) as ws:
            await ws.recv()                                        # hello
            await ws.send(json.dumps({"type": "text", "text": text}))
            deadline = time.time() + 600
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=120)
                except asyncio.TimeoutError:
                    tg.send(chat_id, "(still working)")
                    continue
                if isinstance(raw, (bytes, bytearray)):
                    continue                                       # audio — the phone gets words
                m = json.loads(raw)
                t = m.get("type")
                if t == "say":
                    tg.send(chat_id, m.get("text", ""))
                elif t == "ask":
                    tg.send(chat_id, f"{m.get('speak', '')}\n\n→ {m.get('show', '')}\n\nReply yes or no.")
                    return                                         # the answer comes as a new message
                elif t == "step" and m.get("said"):
                    tg.send(chat_id, f"· {m['said']}")
                elif t == "done":
                    tg.send(chat_id, f"✓ {m.get('summary', '')}")
                    return
                elif t == "state" and m.get("state") == "idle":
                    return
    except Exception as e:                                         # noqa: BLE001
        tg.send(chat_id, f"couldn't reach Vajren on this PC: {type(e).__name__}")


def main() -> None:
    token, allowed = _env()
    tg = Telegram(token)
    print(f"remote: bridging Telegram → {FACE} for one allowed user", flush=True)
    tg.send(allowed, "Vajren is listening here. Say what you need; I'll ask before anything that matters.")
    while True:
        for up in tg.poll():
            msg = up.get("message") or {}
            frm = (msg.get("from") or {}).get("id")
            if frm != allowed:
                continue                                           # rule 1: silence for everyone else
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            asyncio.run(bridge_one(text, tg, allowed))


if __name__ == "__main__":
    main()
