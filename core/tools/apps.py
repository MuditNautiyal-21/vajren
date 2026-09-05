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

    argv = [app] + ([path] if path else [])
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

    # Give it a moment, then confirm it did not die instantly (bad arguments,
    # missing DLL). Still running is success; that is the whole point.
    time.sleep(0.6)
    alive = proc.poll() is None
    return {"app": app, "path": path, "pid": proc.pid, "running": alive,
            "returncode": proc.returncode,
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
