"""
Vajren as a Windows application — a real window, not a browser tab.

    .venv\\Scripts\\pythonw.exe -m core.app        (or double-click Vajren.bat)

WHAT THIS IS: the server from core/server.py started on a background thread,
and a native WebView2 window rendering the face. Taskbar entry, alt-tab, its
own icon, no address bar, no tabs, no browser. Closing the window stops
everything.

WHY NOT AN EXE: it can be one — PyInstaller wraps this file — but the payload
is a 40 GB model directory and a Python environment with torch-free ONNX
runtimes. An exe would be a 30 MB launcher next to 40 GB of data, which is
what this .bat already is, minus the twenty-minute build. If Mudit wants a
double-clickable icon, `Vajren.bat` with a shortcut and an .ico is the same
thing without pretending to be self-contained.

⚠ MICROPHONE: WebView2 denies getUserMedia by default and pywebview exposes no
  permission callback. The env var below is read by WebView2 at startup and is
  the only reliable way to grant it. Without it the window renders perfectly
  and the microphone silently never opens — which looks exactly like the bug
  we already spent an evening on.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Must be set BEFORE webview imports/starts the runtime.
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required")

URL = "http://127.0.0.1:7777"


def _serve() -> None:
    import uvicorn
    from core.server import app
    uvicorn.run(app, host="127.0.0.1", port=7777, log_level="warning")


def _wait_for_server(timeout: float = 90) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(URL + "/status", timeout=2).read()
            return True
        except Exception:                                      # noqa: BLE001
            time.sleep(0.4)
    return False


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    import webview

    threading.Thread(target=_serve, daemon=True).start()
    if not _wait_for_server():
        print("server did not come up — see logs/")
        sys.exit(1)

    window = webview.create_window(
        "Vajren", URL,
        width=1640, height=980, min_size=(1180, 720),
        background_color="#04060b",
        frameless=False, easy_drag=False,
        text_select=False,
    )

    def _ready() -> None:
        # Belt and braces: some WebView2 builds ignore the fake-UI flag unless
        # the page is served from a secure context. 127.0.0.1 counts as one, so
        # this is only here to surface the failure loudly if it ever does not.
        try:
            ok = window.evaluate_js("!!(navigator.mediaDevices && "
                                    "navigator.mediaDevices.getUserMedia)")
            if not ok:
                print("WARNING: getUserMedia is unavailable in this WebView2 build")
        except Exception:                                      # noqa: BLE001
            pass

    webview.start(_ready, private_mode=False, storage_path=str(ROOT / "logs" / "webview"))


if __name__ == "__main__":
    main()
