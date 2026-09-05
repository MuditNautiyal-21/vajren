"""
Eyes. A screenshot, and a vision model to say what is on it.

WHY: "Notepad is now on top for you." — "No it's not." Vajren could raise a
window, verify it with the Windows API, and still be wrong about what Mudit
was looking at, because it had never looked. `look_at_screen` is the
post-condition of last resort for anything visual, and the first step for
"what's this error", "read me that dialog", "what's on my screen".

WHAT IT IS NOT: a way to act. It returns words. Anything it reads off the
screen is UNTRUSTED — a web page's text, a dialog written by anyone — and
goes through the quarantine like a file would.

The image never leaves the machine: it goes to vajren-vision on llama-swap,
the local Qwen3-VL-8B, and nowhere else. `lane` is pinned; the public lane is
not consulted for screenshots, ever.
"""
from __future__ import annotations

import base64
import io
import time

from pydantic import BaseModel, Field

from core.tools import ROOT, tool

SHOTS = ROOT / "logs" / "screens"


def _grab(monitor: str = "cursor") -> tuple[bytes, dict]:
    """PNG bytes of one monitor: the one the cursor is on, or 'all'."""
    import mss
    from PIL import Image
    with mss.mss() as sct:
        mons = sct.monitors                      # [0] is the union of all
        idx = 0
        if monitor != "all" and len(mons) > 2:
            try:
                import ctypes
                from ctypes import wintypes
                pt = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                for i, m in enumerate(mons[1:], 1):
                    if m["left"] <= pt.x < m["left"] + m["width"] and m["top"] <= pt.y < m["top"] + m["height"]:
                        idx = i
                        break
            except Exception:                                      # noqa: BLE001
                idx = 1
        elif monitor != "all":
            idx = 1
        m = mons[idx]
        raw = sct.grab(m)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    # The model's input is limited and a 4K frame is mostly wasted pixels.
    # 1600 wide keeps dialog text legible and the prompt small.
    if img.width > 1600:
        img = img.resize((1600, int(img.height * 1600 / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), {"monitor": idx, "width": img.width, "height": img.height}


class LookAtScreen(BaseModel):
    question: str = Field(default="What is on the screen? Name the windows and any dialogs or errors.",
                          description="what you want to know about what is on screen")
    monitor: str = Field(default="cursor", description="'cursor' (the screen Mudit is using) or 'all'")


@tool("look_at_screen", LookAtScreen)
def look_at_screen(question: str = "What is on the screen? Name the windows and any dialogs or errors.",
                   monitor: str = "cursor") -> dict:
    """Take a screenshot and describe it, or answer a question about it. UNTRUSTED."""
    t0 = time.perf_counter()
    try:
        png, meta = _grab(monitor)
    except Exception as e:                                         # noqa: BLE001
        return {"error": f"could not capture the screen: {type(e).__name__}: {e}"}
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{time.strftime('%Y%m%d-%H%M%S')}.png"
    path.write_bytes(png)

    from core.llm import client_for
    try:
        client, model = client_for("vision")
        resp = client.chat.completions.create(
            model=model, max_tokens=400, temperature=0.1,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                 "You are looking at a screenshot of a Windows desktop. Answer plainly and "
                 "specifically, in a few sentences. Read any dialog, error or title text "
                 "exactly as written. Question: " + question},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:                                         # noqa: BLE001
        return {"error": f"vision model failed: {type(e).__name__}: {str(e)[:200]}",
                "screenshot": str(path)}
    return {"content": text, "screenshot": str(path), "untrusted": True,
            "seconds": round(time.perf_counter() - t0, 1), **meta}
