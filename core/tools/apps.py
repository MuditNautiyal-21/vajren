"""
Opening things — applications, files, folders.

WHY THIS EXISTS: `run_shell` waits for the command to finish. That is correct
for `dir` and catastrophic for `notepad.exe`, which does not finish until a
human closes the window. Asked to "open notepad and write an essay", Vajren
planned `run_shell notepad.exe`, blocked for the full 60-second timeout, then
killed the Notepad it had just opened, then reported failure. It looked like a
hang. It was a shell tool being asked to launch a GUI.

So: launching is a different tool with different semantics. It starts the
process DETACHED, does not wait, and its post-condition is "the process is
running", not "the process exited 0".
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

from core.policy import POLICY
from core.tools import tool

# Programs that never terminate on their own. Naming them is not a
# whitelist — anything may be opened — it is what lets run_shell recognise
# that it is being misused and say something useful instead of hanging.
GUI_APPS = {
    "notepad", "notepad.exe", "wordpad", "write", "mspaint", "paint",
    "explorer", "explorer.exe", "calc", "calculator", "winword", "excel",
    "powerpnt", "code", "devenv", "chrome", "msedge", "firefox", "brave",
    "vlc", "spotify", "discord", "slack", "obs64", "photoshop", "acrobat",
}


# Where Windows programs actually live. `chrome` is not on PATH — the Run
# dialog finds it through the App Paths registry key, and Popen does not look
# there. Without this, "open Chrome" cost FOUR spoken approvals: chrome (no such
# program), msedge (no such program), a recursive Get-ChildItem over Program
# Files to locate it, and finally the full path — for an app that has been
# installed the whole time.
APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

# Shell namespace objects. Keyed with spaces and punctuation stripped, because
# that is how a spoken name arrives ("recycle bin" -> "recyclebin").
SHELL_FOLDERS = {
    "recyclebin": "shell:RecycleBinFolder",
    "trash": "shell:RecycleBinFolder",
    "bin": "shell:RecycleBinFolder",
    "thispc": "shell:MyComputerFolder",
    "mycomputer": "shell:MyComputerFolder",
    "computer": "shell:MyComputerFolder",
    "downloads": "shell:Downloads",
    "documents": "shell:Personal",
    "pictures": "shell:My Pictures",
    "desktop": "shell:Desktop",
    "controlpanel": "shell:ControlPanelFolder",
}

# ⚠ File Explorer windows are titled by the FOLDER they show — "Downloads",
#   "vajren" — so no Explorer window's title has ever contained the string
#   "File Explorer". Measured 2026-09-05: "close the file explorers" searched
#   for 'File Explorer', then 'explorer', found nothing both times, and Vajren
#   said "Closing all File Explorer windows" having closed none. The stable
#   identity of these windows is their WINDOW CLASS, not their caption.
WINDOW_CLASSES = {
    "fileexplorer": ("CabinetWClass", "ExploreWClass"),
    "explorer": ("CabinetWClass", "ExploreWClass"),
    "windowsexplorer": ("CabinetWClass", "ExploreWClass"),
    "folder": ("CabinetWClass", "ExploreWClass"),
    "folders": ("CabinetWClass", "ExploreWClass"),
}


def _classes_for(needle: str) -> tuple[str, ...]:
    """Window classes a spoken window-name should also match, or ()."""
    return WINDOW_CLASSES.get("".join(c for c in needle.lower() if c.isalnum()), ())


def _class_of(hwnd) -> str:
    if sys.platform != "win32":
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.WinDLL("user32", use_last_error=True).GetClassNameW(hwnd, buf, 256)
    return buf.value

# Spoken names people actually use, mapped to the executable to look up.
APP_ALIASES = {
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe", "brave": "brave.exe",
    "notepad": "notepad.exe", "wordpad": "write.exe", "paint": "mspaint.exe",
    "calculator": "calc.exe", "explorer": "explorer.exe",
    "file explorer": "explorer.exe", "word": "winword.exe",
    "excel": "excel.exe", "powerpoint": "powerpnt.exe",
    "vs code": "code.exe", "vscode": "code.exe", "code": "code.exe",
    "terminal": "wt.exe", "spotify": "spotify.exe",
}


_start_apps: dict[str, str] | None = None


def start_apps() -> dict[str, str]:
    """{lowercase display name: AppUserModelID} for everything on the Start menu.

    ⚠ This is how Store apps are found. WhatsApp, Spotify and every other
      Microsoft Store app are on no PATH and in no App Paths key — asked to
      "open WhatsApp", Vajren said "no such program" three times and then
      announced it was opening it anyway. Get-StartApps is the Start menu's own
      list; explorer.exe shell:AppsFolder\<AUMID> is how the Start menu itself
      launches them. Cached for the process: the call costs ~1.5 s.
    """
    global _start_apps
    if _start_apps is not None:
        return _start_apps
    _start_apps = {}
    if sys.platform != "win32":
        return _start_apps
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20, creationflags=0x08000000)
        rows = json.loads(out.stdout or "[]")
        if isinstance(rows, dict):
            rows = [rows]
        for r in rows:
            n, i = str(r.get("Name", "")).strip(), str(r.get("AppID", "")).strip()
            if n and i:
                _start_apps[n.lower()] = i
    except Exception:                                              # noqa: BLE001
        pass
    return _start_apps


def start_app_id(app: str) -> tuple[str, str] | None:
    """(display name, AUMID) for a Start-menu entry matching `app`, exact first."""
    want = app.strip().lower()
    apps = start_apps()
    if want in apps:
        return next((n, i) for n, i in apps.items() if n == want)
    # ⚠ MEASURED: the planner asked for 'whatsappbeta'; Get-StartApps calls it
    #   'whatsapp beta'. `want in name` was False, so Vajren said "no such
    #   program: 'whatsappbeta'" about an app that was installed AND already
    #   open, and burned 139.7 s on the turn. Spoken app names lose their
    #   spaces and punctuation on the way through the planner, so compare with
    #   those removed on BOTH sides before giving up.
    flat = "".join(c for c in want if c.isalnum())
    hits = [(n, i) for n, i in apps.items()
            if want in n or (flat and flat in "".join(c for c in n if c.isalnum()))]
    if not hits:
        # Last resort: every word of the request appears somewhere in the name,
        # so "whatsapp beta" still finds "WhatsApp Beta (Preview)".
        words = [w for w in want.replace("-", " ").replace("_", " ").split() if w]
        hits = [(n, i) for n, i in apps.items() if words and all(w in n for w in words)]
    if not hits:
        return None
    # "whatsapp" matches both "whatsapp" and "whatsapp beta"; prefer the
    # shortest name, which is the plain one, unless the plain one is not there.
    hits.sort(key=lambda x: len(x[0]))
    return hits[0]


def resolve_app(app: str) -> str:
    """Turn a spoken program name into something Popen can actually launch."""
    a = app.strip().strip('"')
    if os.path.sep in a or (len(a) > 1 and a[1] == ":"):
        return a                                   # already a path; leave it alone

    exe = APP_ALIASES.get(a.lower(), a if a.lower().endswith(".exe") else a + ".exe")

    found = shutil.which(exe) or shutil.which(a)
    if found:
        return found

    if sys.platform == "win32":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, f"{APP_PATHS_KEY}\\{exe}") as k:
                    val = winreg.QueryValueEx(k, "")[0].strip('"')
                    if val and Path(val).exists():
                        return val
            except OSError:
                continue
    return a          # let Popen fail with a clear message rather than guessing


# ---------------------------------------------------------- where windows go --
def _bring_here(hwnd) -> dict:
    """
    Move a window onto the monitor the cursor is on, if it is somewhere else.

    ⚠ THE BUG THIS EXISTS FOR. Mudit has three screens. Notepad remembers where
      it was last closed — the right-hand one — so that is where it opened.
      focus_window raised it, Windows agreed it was the foreground window, the
      read-back said VISIBLE, and Vajren said "Notepad is now on top for you"
      to a person looking at the primary screen, where Vajren sits maximised.
      He said "No it's not." Both were right. A window that is in front on a
      screen you are not looking at is not in front.

      "Where the user is" is taken to be the cursor's monitor. Not Vajren's own
      window: he may have dragged that anywhere, and the cursor follows his
      attention more reliably than any window does.
    """
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)

    class MI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    u32.MonitorFromPoint.restype = wintypes.HMONITOR
    u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    u32.MonitorFromWindow.restype = wintypes.HMONITOR
    u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MI)]
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.IsZoomed.argtypes = [wintypes.HWND]

    pt = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    here = u32.MonitorFromPoint(pt, 2)            # MONITOR_DEFAULTTONEAREST
    there = u32.MonitorFromWindow(hwnd, 2)
    if here == there:
        return {"moved": False}

    mi = MI(); mi.cbSize = ctypes.sizeof(MI)
    u32.GetMonitorInfoW(here, ctypes.byref(mi))
    w_ = mi.rcWork
    r = wintypes.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
    was_max = bool(u32.IsZoomed(hwnd))
    if was_max:
        u32.ShowWindow(hwnd, 9)                   # SW_RESTORE, so it can move at all
        u32.GetWindowRect(hwnd, ctypes.byref(r))
    ww = min(r.right - r.left, w_.right - w_.left)
    wh = min(r.bottom - r.top, w_.bottom - w_.top)
    x = w_.left + ((w_.right - w_.left) - ww) // 2
    y = w_.top + ((w_.bottom - w_.top) - wh) // 2
    u32.SetWindowPos(hwnd, None, x, y, ww, wh, 0x0004 | 0x0040)   # NOZORDER|SHOWWINDOW
    if was_max:
        u32.ShowWindow(hwnd, 3)                   # SW_MAXIMIZE, on the new screen
    return {"moved": True, "to": f"monitor at {w_.left},{w_.top}"}


def _raise(hwnd) -> tuple[bool, str, dict]:
    """Bring `hwnd` onto the cursor's monitor and to the front. Returns
    (in_front, how, moved) with in_front READ BACK from the desktop."""
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    u32.BringWindowToTop.argtypes = [wintypes.HWND]
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u32.WindowFromPoint.restype = wintypes.HWND
    u32.WindowFromPoint.argtypes = [wintypes.POINT]
    u32.GetAncestor.restype = wintypes.HWND
    u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

    SW_RESTORE, SW_MINIMIZE = 9, 6
    HWND_TOPMOST, HWND_NOTOPMOST = wintypes.HWND(-1), wintypes.HWND(-2)
    NOZ = 0x2 | 0x1 | 0x40

    def in_front() -> bool:
        if u32.GetForegroundWindow() != hwnd:
            return False
        r = wintypes.RECT()
        if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        pt = wintypes.POINT((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        return u32.GetAncestor(u32.WindowFromPoint(pt), 2) == hwnd

    def attach_and_raise() -> None:
        cur = u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), None)
        mine = k32.GetCurrentThreadId()
        attached = bool(u32.AttachThreadInput(cur, mine, True)) if cur != mine else False
        try:
            u32.BringWindowToTop(hwnd)
            u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u32.AttachThreadInput(cur, mine, False)

    if u32.IsIconic(hwnd):
        u32.ShowWindow(hwnd, SW_RESTORE)
    moved = _bring_here(hwnd)
    attach_and_raise()
    how = "setforeground"
    if not in_front():
        u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, NOZ)
        u32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, NOZ)
        attach_and_raise()
        how = "topmost-flip"
    if not in_front():
        u32.ShowWindow(hwnd, SW_MINIMIZE)
        u32.ShowWindow(hwnd, SW_RESTORE)
        attach_and_raise()
        how = "minimise-restore"
    # Windows animates maximise/restore for ~250 ms; a read-back mid-animation
    # says "not in front" about a window that is about to be. Poll, briefly.
    deadline = time.time() + 0.7
    while time.time() < deadline:
        if in_front():
            return True, how, moved
        time.sleep(0.05)
    return in_front(), how, moved


def _find_window_by_title(sub: str):
    """First visible top-level window whose title contains `sub`, or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        if u32.IsWindowVisible(h):
            n = u32.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(h, b, n + 1)
                if sub.lower() in b.value.lower():
                    found.append(h)
        return True
    u32.EnumWindows(each, 0)
    return found[0] if found else None


def _main_window_of(pid: int, wait_s: float = 3.0):
    """The first visible top-level window owned by `pid`, or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    deadline = time.time() + wait_s
    while time.time() < deadline:
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(h, _):
            owner = wintypes.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(owner))
            if owner.value == pid and u32.IsWindowVisible(h) and u32.GetWindowTextLengthW(h):
                found.append(h)
            return True
        u32.EnumWindows(each, 0)
        if found:
            return found[0]
        time.sleep(0.15)
    return None


class OpenApp(BaseModel):
    app: str = Field(description="program to launch, e.g. notepad, explorer, msedge")
    path: str = Field(default="", description="optional file or folder to open in it")


@tool("open_app", OpenApp, mutating=True)
def open_app(app: str, path: str = "") -> dict:
    """Launch an application, optionally on a file. Returns immediately."""
    if path:
        # A path being opened is read at minimum, so it must clear the policy.
        p = POLICY.assert_path_allowed(path, write=False)
        if not p.exists():
            return {"error": f"nothing at {p} to open"}
        path = str(p)

    # ⚠ Shell folders are not programs. Measured 2026-09-05: "open Recycle Bin"
    #   produced open_app('recyclebin') -> "no such program", then a fallback
    #   that tried to WRITE to C:\$Recycle.Bin and hit writable_roots. Neither
    #   error was the truth: the Recycle Bin is a shell namespace object, and
    #   explorer.exe opens it by moniker exactly as the Start menu does. Same
    #   for This PC and the known user folders.
    if not path:
        shell = SHELL_FOLDERS.get("".join(c for c in app.lower() if c.isalnum()))
        if shell:
            try:
                subprocess.Popen(["explorer.exe", shell], close_fds=True)
            except Exception as e:                                # noqa: BLE001
                return {"error": f"{type(e).__name__}: {e}"}
            time.sleep(1.0)
            return {"app": app, "resolved": shell, "path": "", "shell_folder": True,
                    "launched": True, "running": True, "returncode": 0, "undo_ref": ""}

    exe = resolve_app(app)
    store = None
    if not (os.path.sep in exe or Path(exe).exists()) and not shutil.which(exe):
        store = start_app_id(app)
        if store:
            # A Store app: launch the way the Start menu does. explorer.exe
            # returns at once, so "still running" cannot be the test; the
            # window's appearance is (see below).
            exe = "explorer.exe"
    argv = ([exe, f"shell:AppsFolder\\{store[1]}"] if store
            else [exe] + ([path] if path else []))
    try:
        flags = {}
        if sys.platform == "win32":
            # DETACHED_PROCESS + no window: the app outlives this call and is
            # not killed when Vajren's process tree is cleaned up.
            flags["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True, **flags)
    except FileNotFoundError:
        return {"error": f"no such program: {app!r}"}
    except Exception as e:                                        # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}

    # Give it a moment, then decide whether it actually started.
    #
    # ⚠ "still running after 0.6 s" is NOT the test, though it was. Chrome,
    #   Edge and Word are single-instance: the process you launch hands the
    #   request to the browser that already exists and exits 0 immediately. By
    #   that test Chrome opened on screen and was reported as a failure, so the
    #   planner tried three more ways to open the thing that was already open.
    #   A launcher that exits ZERO has done its job. Only a non-zero exit —
    #   bad arguments, missing DLL — is a failed launch.
    time.sleep(0.6)
    alive = proc.poll() is None
    rc = proc.returncode
    out = {"app": app, "resolved": exe, "path": path, "pid": proc.pid,
           "running": alive, "returncode": rc,
           "launched": alive or rc == 0,
           # Closing the window is the undo, and only Mudit can decide that.
           "undo_ref": ""}
    if store:
        # The launcher is explorer; find the app's window by title instead,
        # waiting up to 6 s for a cold start, and raise it.
        out["store_app"] = store[0]
        deadline = time.time() + 6
        hwnd = None
        while time.time() < deadline and not hwnd:
            hwnd = _find_window_by_title(store[0].split()[0])
            if not hwnd:
                time.sleep(0.3)
        if hwnd:
            front, how, moved = _raise(hwnd)
            out.update({"focused": front, "how": how, "launched": True, **moved})
        else:
            out["launched"] = False
            out["error"] = f"{store[0]} was launched but no window appeared in 6 s"
        return out
    # A window that opens on a screen nobody is looking at has not, in any
    # sense that matters, opened. Put it where the cursor is. Best effort —
    # single-instance apps hand off and exit, so there may be no window to find.
    if alive:
        hwnd = _main_window_of(proc.pid)
        if hwnd:
            # Onto the cursor's monitor AND to the front. A process started
            # from the background gets no foreground rights, so with Chrome
            # maximised the new Notepad sat behind it — the vision model
            # looked and said "no Notepad visible", and it was right.
            # Opened means seen.
            try:
                front, how, moved = _raise(hwnd)
                out.update({"focused": front, "how": how, **moved})
            except Exception:                                      # noqa: BLE001
                pass
    return out


class OpenPath(BaseModel):
    path: str = Field(description="file or folder to open with its default program")


@tool("open_path", OpenPath, mutating=True)
def open_path(path: str) -> dict:
    """Open a file or folder with whatever Windows uses for it by default."""
    p = POLICY.assert_path_allowed(path, write=False)
    if not p.exists():
        return {"error": f"nothing at {p}"}
    try:
        if sys.platform == "win32":
            os.startfile(str(p))                                  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception as e:                                        # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    # A folder opens in Explorer; make sure it lands where he is looking.
    if p.is_dir():
        hwnd = _find_window_by_title(p.name) if False else None
        deadline = time.time() + 3
        while time.time() < deadline and not hwnd:
            hwnd = _find_window_by_title(p.name); time.sleep(0.2) if not hwnd else None
        if hwnd:
            _raise(hwnd)
    return {"path": str(p), "opened": True, "undo_ref": ""}


# ---------------------------------------------------------------- focusing --
class FocusWindow(BaseModel):
    title: str = Field(
        description="part of the window's title bar text, case-insensitive, "
                    "e.g. 'essay' or 'Notepad'")
    size: str = Field(default="", description="'maximize', 'minimize', 'restore' or blank to leave as is")


@tool("focus_window", FocusWindow, mutating=True)
def focus_window(title: str, size: str = "") -> dict:
    """Bring an open window to the front by part of its title; optionally maximize / minimize / restore it."""
    # ⚠ THE BUG THIS EXISTS FOR: "I see Notepad on the taskbar but I can't see
    #   it — bring it to the front." There was no tool for that, so the planner
    #   reached for the nearest thing it had and called open_app("notepad")
    #   with no path. That opens a SECOND, BLANK Notepad. Mudit asked three
    #   times and got three blank windows.
    #
    # ⚠ AND THE BUG IN THE FIRST VERSION OF THIS TOOL, which is worse, because
    #   it is the failure this whole project has a rule against. It returned
    #   whatever SetForegroundWindow returned, and Windows returns TRUE from
    #   that call while doing nothing but flashing the taskbar button, when the
    #   caller does not hold foreground rights. So the tool reported
    #   verified: true, four times in a row, while Mudit sat looking at a
    #   window that had not moved — and then the planner, believing it had
    #   succeeded and been told otherwise, opened a duplicate anyway.
    #
    #   A post-condition must be READ BACK from the world, never taken from the
    #   return value of the thing being checked. GetForegroundWindow() is the
    #   only honest answer to "is it in front".
    if sys.platform != "win32":
        return {"error": "focus_window is Windows-only"}
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Without these, ctypes treats HWNDs as 32-bit ints and truncates real
    # 64-bit handles — the handle you pass back is not the handle you got.
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    u32.BringWindowToTop.argtypes = [wintypes.HWND]
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

    needle = title.strip().lower()
    if not needle:
        return {"error": "give me part of the window title"}

    matches: list[tuple[int, str]] = []
    want_classes = _classes_for(needle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            # Title match, OR the window class this spoken name really means.
            if needle in buf.value.lower() or (
                    want_classes and _class_of(hwnd) in want_classes):
                matches.append((hwnd, buf.value))
        return True

    u32.EnumWindows(each, 0)
    if not matches:
        hint = (" — Explorer windows are named after the folder they show, "
                "so try that folder's name" if want_classes else "")
        return {"error": f"no open window whose title contains {title!r}{hint}"}

    hwnd, found = matches[0]
    # "Maximize the Chrome window" had no tool, so it was focused and reported
    # done. Size is part of "bring it here", and read back like everything else.
    SW = {"maximize": 3, "minimize": 6, "restore": 9}
    want = (size or "").strip().lower()
    if want in SW:
        u32.ShowWindow(hwnd, SW[want])
        time.sleep(0.25)
    if want == "minimize":
        out = {"title": found, "hwnd": int(hwnd), "minimized": bool(u32.IsIconic(hwnd)),
               "focused": True, "how": "minimize", "undo_ref": ""}
        if not out["minimized"]:
            out["error"] = f"{found!r} would not minimize"
        return out
    ok, how, moved = _raise(hwnd)
    out = {"title": found, "hwnd": int(hwnd), "focused": ok, "how": how,
           "others": [t for _, t in matches[1:6]], "undo_ref": "", **moved}
    if want == "maximize":
        u32.IsZoomed.argtypes = [wintypes.HWND]
        out["maximized"] = bool(u32.IsZoomed(hwnd))
        if not out["maximized"]:
            u32.ShowWindow(hwnd, 3); time.sleep(0.2); out["maximized"] = bool(u32.IsZoomed(hwnd))
        if not out["maximized"]:
            out["error"] = f"{found!r} is in front but would not maximize"
    if not ok:
        # Say so. A planner told the truth here asks Mudit; a planner told
        # "verified" opens a second window.
        out["error"] = (f"{found!r} exists but Windows would not bring it forward. "
                        f"Do NOT open it again — a second copy is not what was asked. "
                        f"Tell Mudit to click it on the taskbar.")
    return out


# ----------------------------------------------------------------- the web --
def chrome_profile_dir(name: str) -> str:
    """Turn a profile as a person names it into the folder Chrome wants.

    Chrome's `--profile-directory` takes "Profile 3", not the name on the
    avatar. Mudit says "PCYT". The mapping lives in Chrome's own Local State
    file, so read it rather than guessing.
    """
    if not name:
        return ""
    want = name.strip().lower()
    if want.startswith("profile ") or want == "default":
        return name.strip()
    local_state = Path(os.environ.get("LOCALAPPDATA", "")) / \
        "Google" / "Chrome" / "User Data" / "Local State"
    try:
        info = json.loads(local_state.read_text(encoding="utf-8"))["profile"]["info_cache"]
    except Exception:                                             # noqa: BLE001
        return ""
    # ⚠ Match on the DISPLAY name, and exactly, before anything looser.
    #   On this machine the profile shown as "PcYt" lives in the folder called
    #   `Default`, and there is a *different* profile in a folder literally
    #   named `PCYT` whose display name is "Your Chrome". Matching folder names,
    #   or taking the first fuzzy hit, opens the wrong account — signed in as
    #   somebody else, on a page that looks like it worked.
    keys = ("name", "gaia_name", "user_name", "shortcut_name")
    for exact in (True, False):
        for folder, meta in info.items():
            for key in keys:
                v = str(meta.get(key, "")).strip().lower()
                if not v:
                    continue
                if v == want if exact else (want in v or v in want):
                    return folder
    return ""


class OpenUrl(BaseModel):
    url: str = Field(description="full URL including https://")
    browser: str = Field(default="", description="chrome, edge, firefox — blank for the default")
    profile: str = Field(default="", description="browser profile by the name shown in it, "
                                                 "e.g. PCYT. Chrome only.")


@tool("open_url", OpenUrl, mutating=True)
def open_url(url: str, browser: str = "", profile: str = "") -> dict:
    """Open a web page, optionally in a named browser profile."""
    # ⚠ WHY THIS EXISTS: asked to "open Chrome, pick the PCYT profile and go to
    #   LinkedIn", Vajren could open Chrome and nothing else — there was no
    #   tool that takes a URL. So it improvised with the tools it had and
    #   proposed `Get-Process chrome | Select MainWindowHandle`, which does
    #   nothing at all, and then reported the request finished. A missing tool
    #   does not surface as "I can't"; it surfaces as a confident, useless
    #   plan. This is the third time that pattern has cost a session.
    u = url.strip()
    if not re.match(r"^https?://", u, re.I):
        if "." not in u.split("/")[0]:
            return {"error": f"that does not look like a web address: {url!r}"}
        u = "https://" + u

    if not browser:
        try:
            os.startfile(u) if sys.platform == "win32" else \
                subprocess.Popen(["xdg-open", u])                 # noqa: S606,S607
            return {"url": u, "browser": "default", "opened": True, "undo_ref": ""}
        except Exception as e:                                    # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    exe = resolve_app(browser)
    argv = [exe]
    resolved_profile = ""
    if profile:
        resolved_profile = chrome_profile_dir(profile)
        if not resolved_profile:
            # Naming a profile that cannot be found and opening the default one
            # anyway is worse than not opening it: the page loads, it looks
            # like it worked, and it is signed in as somebody else.
            return {"error": f"no browser profile named {profile!r}. Ask which profile to use, "
                             f"or open it without one."}
        argv.append(f"--profile-directory={resolved_profile}")
    argv.append(u)

    try:
        flags = {}
        if sys.platform == "win32":
            flags["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True, **flags)
    except FileNotFoundError:
        return {"error": f"no such browser: {browser!r}"}
    except Exception as e:                                        # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}

    time.sleep(0.8)
    alive = proc.poll() is None
    rc = proc.returncode
    return {"url": u, "browser": browser, "resolved": exe, "profile": resolved_profile,
            "running": alive, "returncode": rc, "launched": alive or rc == 0, "undo_ref": ""}


# ----------------------------------------------------------------- closing --
class CloseWindow(BaseModel):
    title: str = Field(description="part of the window title, e.g. 'Notepad' or 'essay'")
    all: bool = Field(default=True, description="close every window matching, not just the first")
    force: bool = Field(default=False, description="kill the process if it will not close "
                                                   "politely (loses unsaved work)")


@tool("close_window", CloseWindow, mutating=True)
def close_window(title: str, all: bool = True, force: bool = False) -> dict:
    """Close an open window by part of its title; politely first, by force if asked."""
    # Every session so far has ended with "close that notepad" — and every time
    # it went through run_shell as `taskkill /F /IM notepad.exe`, which kills
    # EVERY Notepad including ones Mudit had open himself, without asking any of
    # them to save. Polite close is WM_CLOSE: the app gets to show its own
    # "Save changes?" dialog, and if it does, that is reported rather than
    # bulldozed. Force is a separate, explicit flag that Mudit has to ask for.
    if sys.platform != "win32":
        return {"error": "close_window is Windows-only"}
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.IsWindow.argtypes = [wintypes.HWND]

    needle = title.strip().lower()
    if not needle:
        return {"error": "give me part of the window title"}
    targets: list[tuple[int, str, int]] = []
    want_classes = _classes_for(needle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        if not u32.IsWindowVisible(h):
            return True
        n = u32.GetWindowTextLengthW(h)
        if not n:
            return True
        b = ctypes.create_unicode_buffer(n + 1); u32.GetWindowTextW(h, b, n + 1)
        # Title match, OR the window class this spoken name really means —
        # "close the file explorers" can only ever work by class.
        if needle in b.value.lower() or (want_classes and _class_of(h) in want_classes):
            pid = wintypes.DWORD(); u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value != os.getpid():                 # never close Vajren itself
                targets.append((h, b.value, pid.value))
        return True
    u32.EnumWindows(each, 0)
    if not targets:
        hint = (" — Explorer windows are named after the folder they show, "
                "so try that folder's name" if want_classes else "")
        return {"error": f"no open window whose title contains {title!r}{hint}"}
    if not all:
        targets = targets[:1]

    WM_CLOSE = 0x0010
    for h, _, _ in targets:
        u32.PostMessageW(h, WM_CLOSE, 0, 0)
    deadline = time.time() + 2.5
    while time.time() < deadline and any(u32.IsWindow(h) for h, _, _ in targets):
        time.sleep(0.1)
    still = [(h, t, p) for h, t, p in targets if u32.IsWindow(h)]

    killed = []
    if still and force:
        PROCESS_TERMINATE = 0x0001
        for _, t, p in still:
            hp = k32.OpenProcess(PROCESS_TERMINATE, False, p)
            if hp:
                k32.TerminateProcess(hp, 1); k32.CloseHandle(hp); killed.append(t)
        time.sleep(0.4)
        still = [(h, t, p) for h, t, p in still if u32.IsWindow(h)]

    closed = [t for h, t, _ in targets if not u32.IsWindow(h)]
    out = {"closed": closed, "count": len(closed), "forced": killed,
           "still_open": [t for _, t, _ in still], "undo_ref": ""}
    if still:
        out["error"] = (f"{len(still)} window(s) did not close — most likely asking to save. "
                        f"Tell Mudit, or call again with force=true if he says to discard.")
    return out
