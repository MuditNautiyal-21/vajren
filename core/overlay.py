"""
The overlay. A small lit core that floats over everything and says, without
being asked, whether Vajren is listening.

    .venv\\Scripts\\python.exe -m core.overlay

Mudit, 2026-09-17: "plan an avatar that can overlay on everything that can let
me know when its listening" â€” and then: "it should come if either vajren moved
to background or is minimized."

So it is not always on screen. It appears exactly when the face cannot be
seen, and gets out of the way the moment it can. The face IS the avatar when
the face is in front; this is the same creature, following him.

WHY NO Qt. The plan called for PySide6, for one property: per-pixel alpha, so
a soft glow does not sit in a grey box. Win32 has had that since Windows 2000
â€” a WS_EX_LAYERED window fed by UpdateLayeredWindow takes a premultiplied
BGRA buffer and composites it properly. numpy is already a dependency and can
paint 128x128 pixels in well under a millisecond. 120 MB of Qt bought nothing
here that ctypes and numpy do not already do.

THE FOUR FLAGS, and what each one is stopping:

  WS_EX_LAYERED      per-pixel alpha at all
  WS_EX_TRANSPARENT  clicks pass THROUGH to whatever is underneath
  WS_EX_NOACTIVATE   the sharpest one. Without it a click here steals focus â€”
                     while Vajren is mid app_type into WhatsApp. Half the
                     message goes to the chat and half goes nowhere.
  WS_EX_TOOLWINDOW   no taskbar button, no Alt-Tab entry. A status light is
                     not an app.

It is a SEPARATE PROCESS on purpose: it must never be able to take the
assistant down, and a 30 fps paint loop must never share a GIL with the
planner. If this dies, Vajren does not notice.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FACE_PORT = int(os.environ.get("VAJREN_FACE_PORT", "7777"))
FACE_URL = f"http://127.0.0.1:{FACE_PORT}"
CONFIG = ROOT / "config" / "overlay.json"

BASE_SIZE = 132                 # px at 100% scaling, the whole window incl. glow
FPS_ACTIVE = 30
FPS_QUIET = 8
IDLE_SLEEP_AFTER = 10.0         # seconds of idle before the loop stops painting

# The face's window title, as ui/index.html sets it. Whatever is hosting the
# page â€” Chrome, Edge, a WebView â€” puts this in the window title, so it is the
# one identifier that survives the browser changing.
FACE_TITLE = "VAJREN"


# ---------------------------------------------------------------- the look --
# One palette, one shape, six states. A state is a set of NUMBERS â€” hue,
# brightness, ring speed, churn â€” interpolated over ~200 ms, never a different
# drawing. Retuning the personality is editing this table.
#
#   hue          0-1, the core's colour
#   glow         overall brightness
#   churn        how much the surface boils. Driven up by his voice.
#   spin         ring speed, turns per second
#   pulse        breathing amplitude
STATES = {
    # âš  offline is dim, not invisible. It was 0.22 and could not be found on a
    #   bright wallpaper â€” which is the same as not being there, and the first
    #   thing he said about this was "I don't see any avatar".
    "offline":           dict(hue=0.58, glow=0.38, churn=0.04, spin=0.02, pulse=0.05),
    "idle":              dict(hue=0.58, glow=0.45, churn=0.10, spin=0.05, pulse=0.10),
    "listening":         dict(hue=0.46, glow=1.00, churn=0.55, spin=0.35, pulse=0.22),
    "transcribing":      dict(hue=0.50, glow=0.80, churn=0.30, spin=0.25, pulse=0.10),
    "thinking":          dict(hue=0.62, glow=0.70, churn=0.18, spin=0.55, pulse=0.06),
    "acting":            dict(hue=0.10, glow=0.85, churn=0.25, spin=0.45, pulse=0.12),
    "awaiting_approval": dict(hue=0.08, glow=1.00, churn=0.20, spin=0.10, pulse=0.45),
    "speaking":          dict(hue=0.52, glow=0.90, churn=0.40, spin=0.20, pulse=0.18),
    "error":             dict(hue=0.00, glow=1.00, churn=0.60, spin=0.00, pulse=0.50),
}
# âš  listening is the brightest, most alive line in that table on purpose. It is
#   the whole reason he asked for this. If he ever has to LOOK to find out
#   whether it is listening, the overlay has failed.


def _hue_rgb(h: float) -> np.ndarray:
    """A small hand-picked ramp â€” cyan through violet to amber and red."""
    stops = [(0.00, (1.00, 0.28, 0.28)),      # red
             (0.10, (1.00, 0.62, 0.20)),      # amber
             (0.46, (0.20, 1.00, 0.85)),      # signal cyan-green: LISTENING
             (0.52, (0.30, 0.85, 1.00)),      # cyan
             (0.58, (0.35, 0.60, 1.00)),      # blue
             (0.62, (0.60, 0.45, 1.00)),      # violet
             (1.00, (1.00, 0.28, 0.28))]
    for (h0, c0), (h1, c1) in zip(stops, stops[1:]):
        if h0 <= h <= h1:
            t = 0.0 if h1 == h0 else (h - h0) / (h1 - h0)
            return np.array([c0[i] + (c1[i] - c0[i]) * t for i in range(3)])
    return np.array(stops[-1][1])


def _become_dpi_aware() -> None:
    """
    âš  At IMPORT, before anything asks Windows about pixels. DPI awareness is
      per-process and effectively one-way, and every answer before it is set â€”
      GetDpiForSystem, GetSystemMetrics, GetWindowRect â€” comes back in
      virtualised coordinates. Setting it inside the window constructor meant
      the size was computed from a lie: 96 DPI on a 120 DPI screen, so the orb
      came out a fifth too small and nothing looked wrong anywhere in the code.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor v2
    except Exception:                                              # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()       # at least system-aware
        except Exception:                                          # noqa: BLE001
            pass


_become_dpi_aware()


def _dpi_size() -> int:
    """
    132 px at 100%, and the same PHYSICAL size at any other scaling.

    âš  This process declares per-monitor DPI awareness v2, so Windows hands it
      real pixels and scales nothing on its behalf. On his 2560x1440 at 125%
      a flat 132 would be a third smaller than it looks in the design. The
      first version had exactly that and the bug was invisible â€” the window
      was correct, the thing MEASURING it was DPI-unaware and reported 106.
      Measure with an aware process or do not measure at all.
    """
    try:
        dpi = ctypes.WinDLL("user32").GetDpiForSystem()
    except Exception:                                              # noqa: BLE001
        dpi = 96
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        base = int(cfg.get("size", BASE_SIZE))
    except Exception:                                              # noqa: BLE001
        base = BASE_SIZE
    return max(64, min(512, round(base * (dpi or 96) / 96)))


SIZE = _dpi_size()

_Y, _X = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
_CX = _CY = (SIZE - 1) / 2.0
_DX = (_X - _CX) / (SIZE * 0.5)
_DY = (_Y - _CY) / (SIZE * 0.5)
_R = np.sqrt(_DX ** 2 + _DY ** 2)
_ANG = np.arctan2(_DY, _DX)


def _smooth(edge0, edge1, x):
    """smoothstep. The whole difference between a crisp edge and a mushy one."""
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


_PX = 2.0 / SIZE                 # one pixel, in the -1..1 space. The AA width.


def render(state: str, t: float, level: float = 0.0, countdown: float = -1.0,
           hover: bool = False) -> bytes:
    """
    One frame, as premultiplied BGRA bytes, top-down.

    âš  REWRITTEN 2026-09-17. Mudit: "The avatar is way too shabby." He was
      right, and the first version was a catalogue of exactly what a design
      review flags as generic: a blue-violet gradient blob, a soft mushy edge,
      and three beating sinusoids standing in for detail. Glow is not design.

      What it is now is an OBJECT â€” an aperture. Every edge is a smoothstep
      one pixel wide, so it is analytically anti-aliased for free instead of
      being blurry to hide the jaggies. The parts are legible and separate:

        core      a lit sphere with a specular highlight and a dark terminator
        iris      one crisp thin ring, the widest at the equator
        ticks     24 fine marks that travel â€” where the motion reads from
        rim       a hairline edge so it holds against a white window
        halo      a narrow falloff outside the rim. Narrow, not a fog bank.

      One hue per state, near-white only at the specular. No gradient soup.
    """
    s = STATES.get(state, STATES["idle"])
    base = _hue_rgb(s["hue"])
    breathe = 1.0 + s["pulse"] * math.sin(t * 2.2) * 0.35
    R_CORE = 0.46 * breathe * (1.0 + level * 0.10)
    glow = s["glow"] * (1.0 + level * 0.40) * (1.12 if hover else 1.0)

    # --- the core: a real sphere. z of the unit hemisphere gives the normal,
    #     so the light and the terminator are shading, not a radial gradient.
    inside = _smooth(R_CORE, R_CORE - 2 * _PX, _R)
    z = np.sqrt(np.clip(1.0 - (_R / R_CORE) ** 2, 0, 1))
    lx, ly, lz = -0.42, -0.52, 0.74                     # light, up and to the left
    lam = np.clip((_DX / R_CORE) * lx + (_DY / R_CORE) * ly + z * lz, 0, 1)
    body = inside * (0.16 + 0.84 * lam ** 1.25)

    # specular: small, tight, and the ONLY place that goes white
    spec = inside * np.clip(lam, 0, 1) ** 26 * 0.9

    # --- the iris: one crisp ring, thicker at the equator so it reads as a
    #     band around a sphere rather than a flat circle drawn on top.
    r_ring = 0.70
    w = (0.016 + 0.014 * np.abs(np.cos(_ANG))) * (1.0 + level * 0.3)
    ring = _smooth(w, w * 0.35, np.abs(_R - r_ring))
    spin = t * s["spin"] * 2.4
    ring = ring * (0.45 + 0.55 * (0.5 + 0.5 * np.cos(_ANG * 2 - spin * 2)))

    # --- the ticks: 24 marks on a wider orbit, travelling the other way.
    #     Fine detail at a fixed count is what stops it looking like a smear.
    r_tick = 0.86
    band = _smooth(0.030, 0.012, np.abs(_R - r_tick))
    phase = _ANG * 12.0 + spin * 1.7
    ticks = band * _smooth(0.55, 0.92, np.cos(phase)) * (0.5 + 0.5 * s["glow"])

    # --- rim: a hairline at the core's edge, so it never dissolves into a
    #     bright window behind it.
    rim = _smooth(2.5 * _PX, 0.5 * _PX, np.abs(_R - R_CORE)) * 0.55

    # --- halo: narrow. A wide one is fog, and fog is what made it shabby.
    halo = np.clip(1.0 - (_R - R_CORE) / 0.16, 0, 1) ** 3 * (_R > R_CORE) * 0.30

    inten = (body + ring * 0.85 + ticks * 0.75 + rim + halo) * glow
    inten = np.clip(inten, 0, 1.4)

    rgb = np.clip(inten[..., None] * base[None, None, :] + spec[..., None] * glow, 0, 1)
    alpha = np.clip(inten * 1.15, 0, 1)

    # --- the readback clock. Information, not mood: it is the only warning
    #     that saying nothing is about to become a decision.
    if countdown >= 0:
        left = float(np.clip(countdown, 0, 1))
        a = (_ANG + math.pi * 2.5) % (math.pi * 2)      # start at 12 o'clock
        arc = (_smooth(0.020, 0.008, np.abs(_R - 0.955))
               * _smooth(0.0, -0.02, a - left * math.pi * 2))
        rgb = np.clip(rgb + arc[..., None] * np.array([1.0, 0.78, 0.30]), 0, 1)
        alpha = np.clip(alpha + arc, 0, 1)

    out = np.empty((SIZE, SIZE, 4), dtype=np.uint8)
    pm = rgb * alpha[..., None]                          # PREMULTIPLIED: required
    out[..., 0] = (pm[..., 2] * 255).astype(np.uint8)    # B
    out[..., 1] = (pm[..., 1] * 255).astype(np.uint8)    # G
    out[..., 2] = (pm[..., 0] * 255).astype(np.uint8)    # R
    out[..., 3] = (alpha * 255).astype(np.uint8)
    return out.tobytes()


# The radius, in the -1..1 space, that counts as "on the orb" for a click.
# Anything outside it is a hole: the click goes to the window underneath.
HIT_R = 0.92

# ------------------------------------------------------------------ win32 --
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
WS_EX_TOPMOST, WS_POPUP = 0x00000008, 0x80000000
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x02, 0x00, 0x01
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
SW_HIDE, SW_SHOWNOACTIVATE, SW_RESTORE = 0, 4, 9
WM_NCHITTEST, WM_LBUTTONDOWN, WM_MOUSEMOVE, WM_LBUTTONUP = 0x0084, 0x0201, 0x0200, 0x0202
WM_MOUSELEAVE, WM_RBUTTONUP, WM_DISPLAYCHANGE = 0x02A3, 0x0205, 0x007E
HTCLIENT, HTTRANSPARENT = 1, -1
DRAG_SLOP = 5                   # px of movement below which a drag is a CLICK


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _prototypes() -> None:
    """
    âš  Every handle-returning call needs an explicit restype on 64-bit Python.
      ctypes defaults to c_int, so an HWND or HDC above 2^31 comes back
      TRUNCATED â€” and a truncated handle does not raise, it just silently
      addresses nothing, which is the worst kind of bug to find by looking at
      a blank screen. The style arguments need it too: WS_POPUP is 0x80000000,
      which does not fit the signed int ctypes guesses (first attempt died on
      exactly that: "argument 11: int too long to convert").
    """
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowLongPtrW.restype = ctypes.c_longlong
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.HDC, ctypes.c_void_p, wintypes.COLORREF,
        ctypes.c_void_p, wintypes.DWORD]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    # interaction
    user32.SetCapture.restype = wintypes.HWND
    user32.SetCapture.argtypes = [wintypes.HWND]
    user32.GetCursorPos.argtypes = [ctypes.c_void_p]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    # ⚠ DefWindowProcW above all. Its LPARAM carries packed coordinates, which
    #   routinely exceed a signed int — without argtypes ctypes guessed c_int
    #   and raised OverflowError INSIDE the window procedure, where the
    #   exception is swallowed ("Exception ignored on calling ctypes callback")
    #   and every single message it did not handle itself quietly failed.
    user32.DefWindowProcW.restype = ctypes.c_longlong
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]


_prototypes()


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def face_is_in_front() -> bool:
    """
    Is the face the window he is looking at right now?

    âš  This is the whole visibility rule, and it is deliberately one question
      rather than two. "Minimised" and "behind something" are the same thing
      from here: the foreground window is not the face, so he cannot see it,
      so the overlay should be there. A minimised window is never the
      foreground window, so no separate IsIconic check is needed â€” and the
      version that DID check both got it wrong when the face was visible but
      unfocused on a second monitor.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return False
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return FACE_TITLE.lower() in buf.value.lower()


def bring_up_the_face() -> str:
    """
    Click the orb, get Vajren. Returns what it did, for the log.

    ⚠ SetForegroundWindow is refused by Windows for a process that does not
      own the foreground — and this process deliberately never takes focus
      (WS_EX_NOACTIVATE), so it is exactly the case Windows blocks. The
      standard, non-hacky unblock is AllowSetForegroundWindow from the window's
      OWNING process, which is the browser, not us. What actually works from
      here without that: restore it, then raise it topmost for an instant and
      immediately drop it back to normal z-order. If Windows still declines,
      the window is at least restored and flashing in the taskbar, which is a
      truthful outcome rather than a silent nothing.
    """
    hwnd = _find_face_window()
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)   # HWND_NOTOPMOST
        user32.SetForegroundWindow(hwnd)
        return "restored"
    # Not open. Only worth opening the page if something is serving it — a
    # browser pointed at a dead port is a worse answer than none.
    try:
        with urllib.request.urlopen(f"{FACE_URL}/status", timeout=1.5):
            pass
    except Exception:                                              # noqa: BLE001
        return "not running"
    os.startfile(FACE_URL)                                         # noqa: S606
    return "opened"


def _find_face_window():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _l):
        if not user32.IsWindowVisible(h) and not user32.IsIconic(h):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n > 0:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if FACE_TITLE.lower() in b.value.lower() and "overlay" not in b.value.lower():
                found.append(h)
                return False
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


class Overlay:
    def __init__(self) -> None:
        self.state = "offline"
        self.level = 0.0
        self.countdown = -1.0
        self.auto_ok_started = 0.0
        self.auto_ok_len = 0.0
        self.last_change = time.time()
        self.hover = False
        self.dragging = False
        self._grab = (0, 0)          # cursor offset within the window at grab
        self._moved = 0              # px travelled, to tell a drag from a click
        self.hwnd = None
        self._make_window()

    # -------------------------------------------------------- interaction --
    def _wndproc(self, h, m, w, l):
        """
        ⚠ Mudit: "I am not able to move this overlay, clicking it should open
          the vajren's window and we should be able to move it!" The first
          version was WS_EX_TRANSPARENT — clicks passed straight through it, so
          it was a picture, not a control.

          Dropping that flag makes it catch clicks, which means it now has to
          answer WM_NCHITTEST honestly: HTCLIENT only where the orb is actually
          drawn, HTTRANSPARENT everywhere else, so the transparent corners stay
          holes and it never eats a click meant for the window underneath.

          WS_EX_NOACTIVATE STAYS. That is the flag that stops a click here
          stealing focus while Vajren is typing into WhatsApp, and it is the
          reason this is safe to make clickable at all.
        """
        if m == WM_NCHITTEST:
            x, y = ctypes.c_short(l & 0xFFFF).value, ctypes.c_short((l >> 16) & 0xFFFF).value
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            dx = (x - (r.left + r.right) / 2) / (SIZE / 2)
            dy = (y - (r.top + r.bottom) / 2) / (SIZE / 2)
            return HTCLIENT if (dx * dx + dy * dy) <= HIT_R ** 2 else HTTRANSPARENT

        if m == WM_LBUTTONDOWN:
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            p = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(p))
            self._grab = (p.x - r.left, p.y - r.top)
            self._moved = 0
            self.dragging = True
            user32.SetCapture(h)
            return 0

        if m == WM_MOUSEMOVE and self.dragging:
            p = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(p))
            nx, ny = p.x - self._grab[0], p.y - self._grab[1]
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            self._moved += abs(nx - r.left) + abs(ny - r.top)
            user32.SetWindowPos(h, HWND_TOPMOST, nx, ny, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
            return 0

        if m == WM_LBUTTONUP and self.dragging:
            self.dragging = False
            user32.ReleaseCapture()
            if self._moved <= DRAG_SLOP:
                self._log("click", bring_up_the_face())
            else:
                self._save_position()
            return 0

        if m == WM_RBUTTONUP:            # a right-click is always "show me"
            self._log("click", bring_up_the_face())
            return 0

        return user32.DefWindowProcW(h, m, w, l)

    def _log(self, kind: str, detail: str) -> None:
        try:
            with (ROOT / "logs" / "overlay.log").open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {kind}: {detail}\n")
        except Exception:                                          # noqa: BLE001
            pass

    def _save_position(self) -> None:
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
        except Exception:                                          # noqa: BLE001
            cfg = {}
        cfg.update({"x": r.left, "y": r.top})
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:                                          # noqa: BLE001
            pass

    # ------------------------------------------------------------- window --
    def _make_window(self) -> None:
        # DPI awareness is already set, at import â€” see _become_dpi_aware.
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND,
                                     wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self._proc = WNDPROC(self._wndproc)
        cls = WNDCLASS()
        cls.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        cls.lpszClassName = "VajrenOverlay"
        cls.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        user32.RegisterClassW(ctypes.byref(cls))

        x, y = self._saved_position()
        self.hwnd = user32.CreateWindowExW(
            # ⚠ NO WS_EX_TRANSPARENT any more — it is what made this a picture
            #   instead of a control. WM_NCHITTEST does the same job better:
            #   the corners are still holes, but the orb itself catches the
            #   click. WS_EX_NOACTIVATE is what keeps that safe.
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
            "VajrenOverlay", "Vajren", WS_POPUP,
            x, y, SIZE, SIZE, None, None, cls.hInstance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.visible = True

    def _saved_position(self) -> tuple[int, int]:
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        default = (sw - SIZE - 24, sh - SIZE - 72)      # above the tray
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            x, y = int(cfg["x"]), int(cfg["y"])
            if -SIZE < x < sw and -SIZE < y < sh:       # a stale position from a
                return x, y                             # monitor he unplugged
        except Exception:                                          # noqa: BLE001
            pass
        return default

    def paint(self, buf: bytes) -> None:
        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = SIZE, -SIZE         # negative = top-down
        bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(hdc_mem, dib)
        ctypes.memmove(bits, buf, len(buf))

        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        size = wintypes.SIZE(SIZE, SIZE)
        src = wintypes.POINT(0, 0)
        user32.UpdateLayeredWindow(self.hwnd, hdc_screen, None, ctypes.byref(size),
                                   hdc_mem, ctypes.byref(src), 0,
                                   ctypes.byref(blend), ULW_ALPHA)
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(dib)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

    def show(self, on: bool) -> None:
        if on == getattr(self, "visible", None):
            return
        self.visible = on
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE if on else SW_HIDE)
        if on:                                   # something else may have taken
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,   # topmost
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    # -------------------------------------------------------------- state --
    def listen(self) -> None:
        """Follow /state forever. Never lets a dead server look like idle."""
        while True:
            try:
                with urllib.request.urlopen(f"{FACE_URL}/state", timeout=20) as r:
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        self._apply(json.loads(line[5:].strip()))
            except Exception:                                      # noqa: BLE001
                # âš  offline, not idle. A dead face must not look like a calm
                #   one â€” that is the failure where he talks to a machine that
                #   stopped listening an hour ago and never said so.
                self._set("offline")
                time.sleep(2.0)

    def _apply(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "state":
            self._set(str(msg.get("state") or "idle"))
        elif t == "error":
            self._set("error")
        elif t == "step":
            self._set("acting")
        elif t == "progress" and msg.get("stage") in ("act", "verify"):
            self._set("acting")
        elif t == "ask":
            self._set("awaiting_approval")
            secs = msg.get("auto_ok")
            if secs:
                self.auto_ok_started, self.auto_ok_len = time.time(), float(secs)
        if msg.get("level") is not None:
            try:
                self.level = max(0.0, min(1.0, float(msg["level"])))
            except (TypeError, ValueError):
                pass

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.last_change = time.time()
            if state != "awaiting_approval":
                self.auto_ok_len = 0.0

    # --------------------------------------------------------------- loop --
    # How long it announces itself when it starts, in seconds. Not vanity:
    # something that appears silently in a corner he was not looking at has
    # not appeared at all, and a thing whose whole job is "you can see me"
    # should prove it once when it wakes up.
    INTRO = 1.4

    def run(self) -> None:
        threading.Thread(target=self.listen, daemon=True, name="overlay-state").start()
        msg = wintypes.MSG()
        t0 = time.time()
        while True:
            # Pump, or Windows decides the process is hung and greys it out.
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            wanted = not face_is_in_front()
            self.show(wanted)

            if self.auto_ok_len:
                left = 1.0 - (time.time() - self.auto_ok_started) / self.auto_ok_len
                self.countdown = left if left > 0 else -1.0
                if left <= 0:
                    self.auto_ok_len = 0.0
            else:
                self.countdown = -1.0

            age = time.time() - t0
            intro = max(0.0, 1.0 - age / self.INTRO)    # a swell that settles
            busy = (self.state not in ("idle", "offline") or self.countdown >= 0
                    or intro > 0 or self.dragging)
            if self.visible and (busy or time.time() - self.last_change < IDLE_SLEEP_AFTER):
                self.paint(render(self.state, age, max(self.level, intro),
                                  self.countdown, self.dragging))
                # ⚠ A drag has to be pumped at 60 Hz or the orb lags the cursor
                #   by a quarter of a second and feels broken — the message
                #   pump and the paint loop are the same loop here.
                time.sleep(1.0 / (60 if self.dragging else FPS_ACTIVE if busy else FPS_QUIET))
            else:
                # âš  Idle and nothing happening: paint ONCE and stop. A status
                #   light that spins his fans is a status light he turns off,
                #   and then it is not telling him anything at all.
                if self.visible:
                    self.paint(render(self.state, time.time() - t0, 0.0, -1.0))
                time.sleep(0.25)


def main() -> None:
    if not sys.platform.startswith("win"):
        print("The overlay is Windows-only (layered windows).")
        return 1
    Overlay().run()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
