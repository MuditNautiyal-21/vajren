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

import os
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

    exe = resolve_app(app)
    argv = [exe] + ([path] if path else [])
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
    return {"app": app, "resolved": exe, "path": path, "pid": proc.pid,
            "running": alive, "returncode": rc,
            "launched": alive or rc == 0,
            # Closing the window is the undo, and only Mudit can decide that.
            "undo_ref": ""}


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
    return {"path": str(p), "opened": True, "undo_ref": ""}


# ---------------------------------------------------------------- focusing --
class FocusWindow(BaseModel):
    title: str = Field(
        description="part of the window's title bar text, case-insensitive, "
                    "e.g. 'essay' or 'Notepad'")


@tool("focus_window", FocusWindow, mutating=True)
def focus_window(title: str) -> dict:
    """Bring an already-open window to the front by part of its title."""
    # ⚠ THE BUG THIS EXISTS FOR: "I see Notepad on the taskbar but I can't see
    #   it — bring it to the front." There was no tool for that, so the planner
    #   reached for the nearest thing it had and called open_app("notepad")
    #   with no path. That opens a SECOND, BLANK Notepad. Mudit asked three
    #   times, got three blank windows, and then had to ask for both to be
    #   force-closed. Raising a window is not opening an application, and a
    #   planner with only a hammer will keep producing nails.
    if sys.platform != "win32":
        return {"error": "focus_window is Windows-only"}
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    needle = title.strip().lower()
    if not needle:
        return {"error": "give me part of the window title"}

    matches: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            if needle in buf.value.lower():
                matches.append((hwnd, buf.value))
        return True

    u32.EnumWindows(each, 0)
    if not matches:
        return {"error": f"no open window whose title contains {title!r}"}

    hwnd, found = matches[0]
    SW_RESTORE = 9
    if u32.IsIconic(hwnd):
        u32.ShowWindow(hwnd, SW_RESTORE)
    # SetForegroundWindow is refused for a process that does not own the
    # foreground, so borrow the foreground thread's input queue for the call.
    # Without this the window un-minimises and stays behind everything, which
    # is indistinguishable from doing nothing.
    cur = u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), None)
    mine = ctypes.windll.kernel32.GetCurrentThreadId()
    u32.AttachThreadInput(cur, mine, True)
    try:
        u32.BringWindowToTop(hwnd)
        ok = bool(u32.SetForegroundWindow(hwnd))
    finally:
        u32.AttachThreadInput(cur, mine, False)

    return {"title": found, "hwnd": int(hwnd), "focused": ok,
            "others": [t for _, t in matches[1:6]], "undo_ref": ""}
