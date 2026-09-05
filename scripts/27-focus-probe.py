"""27 - Reproduce 'Notepad is now on top' with no Notepad on top.

Drives the LIVE face over its websocket (the real server process, the real
window) with a typed request, then reads the world back independently:
which window is foreground, and which window is at Notepad's own centre.

    .venv\\Scripts\\python.exe -X utf8 scripts\\27-focus-probe.py
"""
from __future__ import annotations

import asyncio, ctypes, json, os, subprocess, sys, time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetForegroundWindow.restype = wintypes.HWND
u32.WindowFromPoint.restype = wintypes.HWND
u32.WindowFromPoint.argtypes = [wintypes.POINT]
u32.GetAncestor.restype = wintypes.HWND
u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


def title(h):
    n = u32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1); u32.GetWindowTextW(h, b, n + 1); return b.value


def find(sub):
    out = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        if u32.IsWindowVisible(h) and sub.lower() in title(h).lower(): out.append(h)
        return True
    u32.EnumWindows(each, 0); return out


def world(tag):
    fg = u32.GetForegroundWindow()
    print(f"  [{tag}] foreground = {title(fg)!r}")
    for h in find("notepad"):
        r = wintypes.RECT(); u32.GetWindowRect(h, ctypes.byref(r))
        pt = wintypes.POINT((r.left + r.right)//2, (r.top + r.bottom)//2)
        top = u32.GetAncestor(u32.WindowFromPoint(pt), 2)
        print(f"          notepad {title(h)!r} rect={r.left},{r.top}-{r.right},{r.bottom}"
              f"  at-its-centre = {title(top)!r}  {'VISIBLE' if top == h else 'COVERED'}")
    for h in find("vajren"):
        r = wintypes.RECT(); u32.GetWindowRect(h, ctypes.byref(r))
        print(f"          vajren  rect={r.left},{r.top}-{r.right},{r.bottom}")


async def main():
    import websockets
    subprocess.Popen(["notepad.exe"], creationflags=0x8 | 0x200); time.sleep(1.5)
    world("before")
    port = os.environ.get("VAJREN_FACE_PORT", "7777")
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as ws:
        await ws.send(json.dumps({"type": "text", "text": "bring the notepad window to the front"}))
        t0 = time.time()
        while time.time() - t0 < 90:
            m = await ws.recv()
            if isinstance(m, bytes):
                await ws.send(json.dumps({"type": "played"})); continue
            e = json.loads(m)
            if e.get("type") == "step":
                print(f"  step {e['tool']} verified={e['verified']} err={e.get('error')}")
                world("right after step")
            if e.get("type") == "say":
                print(f"  said: {e['text']!r}")
            if e.get("type") == "done":
                break
    time.sleep(1.0); world("1s later")
    time.sleep(3.0); world("4s later")

asyncio.run(main())
