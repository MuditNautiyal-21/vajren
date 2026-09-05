"""
run_shell — the most dangerous tool in the box, so the most constrained.

What "sandboxed" means here, concretely:
  cwd      forced inside a writable root (policy.yaml). Default: sandbox/.
  env      built from scratch. The child gets PATH and a handful of system
           variables and NOTHING else — no GROQ_API_KEY, no LITELLM_MASTER_KEY.
           A shell that inherits the parent env is a one-liner away from
           `echo $env:OPENROUTER_API_KEY`, and the planner reads the output.
  timeout  hard, default 60 s, ceiling 300 s. On expiry the whole process TREE
           is killed (taskkill /T), not just the shell — otherwise a hung child
           outlives the tool and the loop believes it has stopped.
  denylist regexes checked against the raw command before anything spawns.
           Not a substitute for the confirm gate; a second fence behind it.
  output   capped at 64 KB per stream and tagged untrusted. Command output is
           the same class of data as an email body.

What it does NOT do: it does not stop a confirmed command from doing what the
human confirmed. That is the point of the gate. This tool's job is to make sure
the human confirmed the command that actually runs — graph.py speaks the
literal command, never the model's paraphrase of it.

Proof of work: `expect_path`. A command that claims to have produced something
names the path; verify.py checks it exists. returncode==0 alone is "it said so".
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from core.policy import POLICY
from core.tools import ROOT, tool

SANDBOX = ROOT / "sandbox"
MAX_OUT = 65_536
CEILING_S = 300

# Belt, for when the braces (the gate) are off. Case-insensitive. Deliberately
# blunt — false positives cost a re-phrase, false negatives cost a filesystem.
DENY = [re.compile(p, re.I) for p in (
    # ⚠ Precision matters here. This was `\bformat(-volume)?\b`, which also
    #   matched `Get-Date -Format` — so a harmless date lookup was refused, and
    #   the planner, unable to read the real date, wrote one from memory and got
    #   it wrong by a year. An over-broad denylist does not fail safe; it pushes
    #   the model toward guessing.
    r"\bFormat-Volume\b", r"\bformat(\.com|\.exe)?\s+[a-z]:", r"\bformat\s+/",
    r"\bdiskpart\b", r"\bbcdedit\b", r"\bvssadmin\b",
    r"\bcipher\s+/w", r"\breg(\.exe)?\s+(add|delete|import)\b", r"\bregedit\b",
    r"\b(net|net1)\s+(user|localgroup|share)\b", r"\bschtasks\b", r"\bsc(\.exe)?\s+(create|config|delete)\b",
    r"\bicacls\b", r"\btakeown\b", r"\bshutdown\b", r"\brestart-computer\b", r"\bstop-computer\b",
    r"\bset-executionpolicy\b", r"-enc(odedcommand)?\b", r"\b(iex|invoke-expression)\b",
    r"(iwr|invoke-webrequest|curl|wget)[^|\n]*\|\s*(iex|powershell|cmd|sh|bash)",
    r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\s+[\\/]", r"\b(rd|rmdir)\s+/s\b", r"\bdel\s+/[sq]\b",
    r"remove-item[^\n]*-recurse[^\n]*\b[a-z]:\\?(\s|$|\")",  # whole-drive recursive delete
    r"\\(policy\.yaml|\.env|CLAUDE\.md)\b", r"\.ssh\b", r"\bDefender\b", r"\bMpPreference\b",
    r"\bnew-service\b", r"\bnssm\b", r"\bruna?s\b", r"\bstart-process[^\n]*-verb\s+runas",
)]

# The child environment. Explicit allowlist — anything not here does not exist.
_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
         "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "NUMBER_OF_PROCESSORS", "OS")


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _KEEP}
    env["PYTHONIOENCODING"] = "utf-8"
    env["VAJREN_SANDBOX"] = "1"
    return env


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.killpg(os.getpgid(pid), 9)


class RunShell(BaseModel):
    command: str = Field(description="the exact command line to run in PowerShell")
    cwd: str = Field(default=str(SANDBOX), description="working directory; must be a writable root")
    timeout_s: int = Field(default=60, ge=1, le=CEILING_S)
    expect_path: str = Field(default="", description="a path that must exist afterwards, as proof the command worked")
    nonce: str = Field(default="", description="set to anything new to force a re-run of an identical command")


@tool("run_shell", RunShell, mutating=True)
def run_shell(command: str, cwd: str = str(SANDBOX), timeout_s: int = 60,
              expect_path: str = "", nonce: str = "") -> dict:
    """Run one PowerShell command in the sandbox. Output is UNTRUSTED data."""
    for rx in DENY:
        if rx.search(command):
            return {"error": f"command denied by pattern {rx.pattern!r}", "command": command}

    wd = POLICY.assert_path_allowed(cwd, write=True)
    wd.mkdir(parents=True, exist_ok=True)
    if expect_path:
        POLICY.assert_path_allowed(expect_path, write=False)

    if sys.platform == "win32":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", command]
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        argv = ["bash", "-lc", command]
        flags = {"start_new_session": True}

    proc = subprocess.Popen(argv, cwd=str(wd), env=_clean_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        out, err = proc.communicate()

    def _txt(b: bytes) -> str:
        s = b.decode("utf-8", "replace")
        return s if len(s) <= MAX_OUT else s[:MAX_OUT] + f"\n...[truncated {len(s) - MAX_OUT} chars]"

    result = {
        "command": command, "cwd": str(wd), "returncode": proc.returncode,
        "stdout": _txt(out), "stderr": _txt(err), "timed_out": timed_out, "untrusted": True,
        # run_shell has no generic undo: the command is what it was. What it CAN
        # do is name what it produced, so the caller can trash it.
        "undo_ref": f"produced||{expect_path}" if expect_path else "",
    }
    if expect_path:
        result["expect_path"] = expect_path
        result["expect_path_exists"] = Path(expect_path).exists()
    return result
