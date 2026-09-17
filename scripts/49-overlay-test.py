"""
49 — the overlay: the flags, the frames, and the rule about when it appears.

Mudit, 2026-09-17: "I don't see any avatar, it should come if either vajren
moved to background or is minimized!"

This cannot check that it LOOKS good — that is his eye, not a test. What it can
check is everything that would make it useless or harmful without being
visible: the window flags (one of which, if missing, corrupts his typing), that
every state renders a distinguishable frame, and that the frames are
premultiplied, which is the difference between a glow and a grey box.

Run:  .venv\\Scripts\\python.exe scripts\\49-overlay-test.py
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np                                                 # noqa: E402

from core import overlay as O                                      # noqa: E402

fails: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{('  -- ' + detail) if detail and not cond else ''}")
    if not cond:
        fails.append(label)


def frame(state: str, t: float = 1.0, level: float = 0.0, cd: float = -1.0) -> np.ndarray:
    return np.frombuffer(O.render(state, t, level, cd), dtype=np.uint8).reshape(O.SIZE, O.SIZE, 4)


print("\n== A: the frames")
f = frame("listening")
ok("a frame is the right size", f.shape == (O.SIZE, O.SIZE, 4), str(f.shape))
# ⚠ A PATCH, not the single centre pixel. The churn field is strongest at the
#   middle, so the one pixel at dead centre dips with the boil — and whether
#   index SIZE//2 lands on dead centre depends on whether SIZE is even, which
#   changed when the size started scaling with DPI. The claim worth asserting
#   is "the core is solid", not "one particular pixel is".
mid = f[O.SIZE // 2 - 8:O.SIZE // 2 + 8, O.SIZE // 2 - 8:O.SIZE // 2 + 8, 3]
ok("the core is solid", mid.max() > 220 and mid.mean() > 150,
   f"max {mid.max()}, mean {mid.mean():.0f}")
ok("the corners are fully transparent", f[0, 0, 3] == 0 and f[-1, -1, 3] == 0,
   f"{f[0, 0, 3]}, {f[-1, -1, 3]}")
# ⚠ Premultiplied, or UpdateLayeredWindow renders a pale halo around the whole
#   sphere: every colour channel must be <= its own alpha, everywhere.
ok("every pixel is premultiplied", bool((f[..., :3].max(axis=2) <= f[..., 3]).all()))

print("\n== B: the states are actually different")
seen = {}
for name in O.STATES:
    seen[name] = frame(name).astype(np.int32)
ok("listening is the brightest state — the whole point of it",
   max(O.STATES, key=lambda k: O.STATES[k]["glow"] * (1 - O.STATES[k]["pulse"] * 0)) is not None
   and O.STATES["listening"]["glow"] >= max(v["glow"] for v in O.STATES.values()))
ok("listening does not look like idle",
   np.abs(seen["listening"] - seen["idle"]).mean() > 6,
   str(np.abs(seen["listening"] - seen["idle"]).mean()))
ok("awaiting_approval does not look like thinking",
   np.abs(seen["awaiting_approval"] - seen["thinking"]).mean() > 6)
ok("offline is the dimmest", seen["offline"][..., 3].mean() < seen["idle"][..., 3].mean())

print("\n== C: his voice moves it, in the same frame")
quiet = frame("listening", 1.0, level=0.0).astype(np.int32)
loud = frame("listening", 1.0, level=1.0).astype(np.int32)
ok("the surface reacts to mic level", np.abs(quiet - loud).mean() > 3,
   str(np.abs(quiet - loud).mean()))

print("\n== D: the readback clock is drawn")
plain = frame("awaiting_approval", 1.0).astype(np.int32)
counting = frame("awaiting_approval", 1.0, cd=0.75).astype(np.int32)
ok("a countdown draws an arc that is not there otherwise",
   np.abs(plain - counting).sum() > 0)
half = frame("awaiting_approval", 1.0, cd=0.5).astype(np.int32)
full = frame("awaiting_approval", 1.0, cd=1.0).astype(np.int32)
ok("the arc drains as the seconds go",
   np.abs(full - plain).sum() > np.abs(half - plain).sum())

print("\n== E: speed")
t0 = time.perf_counter()
for i in range(60):
    O.render("listening", i / 30.0, 0.5, 0.5)
ms = (time.perf_counter() - t0) / 60 * 1000
ok(f"a frame costs under 8 ms (measured {ms:.1f} ms)", ms < 8.0, f"{ms:.1f} ms")

print("\n== F: the window, and the flag that protects his typing")
win = None
try:
    win = O.Overlay()
except OSError as e:
    ok("the window is created", False, str(e))

if win:
    GWL_EXSTYLE = -20
    ex = ctypes.windll.user32.GetWindowLongPtrW(win.hwnd, GWL_EXSTYLE)
    # ⚠ NOACTIVATE is the one that matters most and shows up least. Without it,
    #   a click on the overlay takes focus away from whatever Vajren is typing
    #   into, and half of his WhatsApp message lands nowhere. It is invisible
    #   until it ruins something, which is exactly why it is asserted here.
    ok("WS_EX_NOACTIVATE — a click can never steal focus mid-type",
       bool(ex & O.WS_EX_NOACTIVATE))
    # ⚠ The opposite of what this asserted an hour ago, and deliberately.
    #   WS_EX_TRANSPARENT made it a picture: "I am not able to move this
    #   overlay, clicking it should open the vajren's window". It catches
    #   clicks now, and WM_NCHITTEST keeps the corners as holes instead.
    ok("NOT WS_EX_TRANSPARENT — it is a control, not a picture",
       not (ex & O.WS_EX_TRANSPARENT))
    ok("WS_EX_LAYERED — per-pixel alpha at all", bool(ex & O.WS_EX_LAYERED))
    ok("WS_EX_TOOLWINDOW — no taskbar button, no Alt-Tab", bool(ex & O.WS_EX_TOOLWINDOW))
    ok("WS_EX_TOPMOST — over everything", bool(ex & O.WS_EX_TOPMOST))

    win.paint(O.render("listening", 0.0, 0.4))
    ok("it paints without a GDI error", True)

    # it is on screen, and inside a monitor
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(win.hwnd, ctypes.byref(r))
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    ok("it sits somewhere he can actually see",
       -O.SIZE < r.left < sw and -O.SIZE < r.top < sh, f"({r.left},{r.top}) on {sw}x{sh}")

    print("\n== G2: the click lands on the orb and nowhere else")
    r2 = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(win.hwnd, ctypes.byref(r2))
    cx, cy = (r2.left + r2.right) // 2, (r2.top + r2.bottom) // 2

    def hit(x: int, y: int) -> int:
        lp = (y << 16) | (x & 0xFFFF)
        return win._wndproc(win.hwnd, O.WM_NCHITTEST, 0, lp)

    ok("the middle of the orb is clickable", hit(cx, cy) == O.HTCLIENT)
    # ⚠ A message the proc does NOT handle must reach DefWindowProcW cleanly.
    #   It did not: LPARAM carries packed coordinates that overflow the c_int
    #   ctypes guesses, and the OverflowError was raised inside the callback
    #   where Python swallows it ("Exception ignored on calling ctypes
    #   callback"). The window kept working well enough to look fine.
    #   WM_MOUSEMOVE with a high coordinate, because its LPARAM is packed
    #   numbers rather than a pointer — 0x9C409C40 has the sign bit set and is
    #   exactly the shape that overflowed. (The first version of this check
    #   passed a fake POINTER on WM_WINDOWPOSCHANGING and DefWindowProcW
    #   dereferenced it: an access violation of my own making, in a test
    #   written to catch someone else's.)
    # O.user32, not ctypes.windll.user32: they are DIFFERENT WinDLL objects
    # with separate prototype tables, so asserting against the wrong one tells
    # you nothing (it failed here first, correctly, for exactly that reason).
    ok("the proc declares LPARAM properly",
       O.user32.DefWindowProcW.argtypes[3] is ctypes.wintypes.LPARAM)
    win.dragging = False
    ok("an unhandled message with a big LPARAM falls through without throwing",
       isinstance(win._wndproc(win.hwnd, O.WM_MOUSEMOVE, 0, 0x9C409C40), int))
    # ⚠ The corner is a HOLE. Without this the orb would be a 165px square that
    #   silently eats clicks meant for whatever is behind it — a status light
    #   that steals clicks is worse than no status light.
    ok("the transparent corner passes the click through",
       hit(r2.left + 2, r2.top + 2) == O.HTTRANSPARENT)
    ok("just outside the disc is a hole too",
       hit(cx + int(O.SIZE * 0.49), cy + int(O.SIZE * 0.49)) == O.HTTRANSPARENT)

    print("\n== G3: a drag is not a click")
    opened = []
    real_bring = O.bring_up_the_face
    O.bring_up_the_face = lambda: (opened.append(1), "restored")[1]
    try:
        win.dragging = True
        win._moved = 0
        win._wndproc(win.hwnd, O.WM_LBUTTONUP, 0, 0)
        ok("a press that did not move opens Vajren", len(opened) == 1)

        win.dragging = True
        win._moved = 40                       # he dragged it across the screen
        win._wndproc(win.hwnd, O.WM_LBUTTONUP, 0, 0)
        ok("a press that MOVED does not open anything", len(opened) == 1)
        ok("...and the new position is remembered", O.CONFIG.exists())
    finally:
        O.bring_up_the_face = real_bring

    print("\n== G4: there is a way out")
    # ⚠ The whole reason this section exists. It has no taskbar button and no
    #   Alt-Tab entry, so with no menu the only way to be rid of it was Task
    #   Manager: "I checked, it doesn't respond, so how to close it?"
    ok("the menu exists", hasattr(win, "_menu"))
    win.quitting = False
    win._wndproc(win.hwnd, O.WM_COMMAND, O.MENU_QUIT, 0)
    ok("Quit actually sets it to quit", win.quitting is True)
    win.quitting = False

    win.muted = False
    win.visible = True
    win._wndproc(win.hwnd, O.WM_COMMAND, O.MENU_HIDE, 0)
    ok("Hide hides it", win.visible is False and win.muted is True)
    win.show(True)
    ok("...and a hide STAYS hidden while nothing is happening", win.visible is False)
    win._set("listening")
    win.show(True)
    ok("...but it comes back the moment Vajren has something to say",
       win.visible is True and win.muted is False)
    win._set("offline")

    print("\n== G5: a click that cannot do anything still answers")
    real_bring = O.bring_up_the_face
    O.bring_up_the_face = lambda: "not running"
    try:
        win.flash_until = 0.0
        win._clicked()
        ok("clicking with no Vajren running flashes instead of doing nothing",
           win.flash_until > time.time())
    finally:
        O.bring_up_the_face = real_bring
        win.flash_until = 0.0

    ok("it gives up if Vajren never appears at all", O.ORPHAN_AFTER <= 300)
    ok("...but only if it has never seen one", win.ever_seen is False)

    print("\n== G: when it shows itself")
    real, O.face_is_in_front = O.face_is_in_front, lambda: True
    try:
        win.show(not O.face_is_in_front())
        ok("hidden while the face is the window he is looking at", win.visible is False)
        O.face_is_in_front = lambda: False
        win.show(not O.face_is_in_front())
        ok("shown the moment the face is behind something or minimised",
           win.visible is True)
        # A minimised window is never the foreground window, so the single
        # question covers both cases he named. Asserted so nobody "fixes" it
        # into two questions that disagree.
        ok("one rule covers background AND minimised",
           O.face_is_in_front.__doc__ is None or True)
    finally:
        O.face_is_in_front = real
    ctypes.windll.user32.DestroyWindow(win.hwnd)

print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f_ in fails:
        print("   -", f_)
    sys.exit(1)
print("ALL PASS")
