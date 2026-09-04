"""
What is missing, and how to get it — cross-platform.

The whole point: VAJREN lands on an unknown machine, works out what it needs,
and fetches it. But there is a line, and it is drawn by SCOPE, not by convenience:

  SELF-SCOPED  — everything installed inside VAJREN's own directory or its own
                 virtual environment. Python packages, llama.cpp binaries, GGUF
                 weights, ffmpeg, MCP servers. Nothing outside the tree changes,
                 nothing needs admin, and deleting the folder undoes all of it.
                 One consent, up front, then it proceeds.

  SYSTEM-SCOPED — GPU drivers, CUDA/ROCm toolkits, system PATH, services,
                  package managers. These need elevation and change the machine
                  for every user on it. VAJREN prints exactly what to run and
                  waits for a human. It never runs them itself, and never asks
                  for admin on its own behalf. See J-025 and config/policy.yaml.

In practice almost everything falls in the first bucket, so "download the things
that are missing and configure itself" is real — it just stops at the driver.
"""
from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = platform.system()
MACHINE = platform.machine().lower()


@dataclass
class Need:
    key: str
    what: str                 # human sentence, spoken and printed
    scope: str                # "self" | "system"
    size_mb: int = 0
    present: bool = False
    how: str = ""             # command or URL, for the system-scoped ones
    detail: str = ""


# --------------------------------------------------------------------------- #
#  Probes
# --------------------------------------------------------------------------- #
def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _llama_binary() -> Path:
    exe = "llama-server.exe" if SYSTEM == "Windows" else "llama-server"
    return ROOT / "llama" / exe


def _models_present() -> list[str]:
    d = ROOT / "models"
    return [p.name for p in d.glob("*.gguf")] if d.exists() else []


# --------------------------------------------------------------------------- #
#  The manifest
# --------------------------------------------------------------------------- #
PY_CORE = [
    ("yaml", "pyyaml"), ("pydantic", "pydantic"), ("openai", "openai"),
    ("instructor", "instructor"), ("dotenv", "python-dotenv"),
    ("psutil", "psutil"), ("httpx", "httpx"),
]
PY_AGENT = [
    ("langgraph", "langgraph"), ("langchain_core", "langchain-core"),
    ("litellm", "litellm"),
]
PY_MEMORY = [("sqlite_vec", "sqlite-vec"), ("watchdog", "watchdog")]
PY_VOICE = [
    ("openwakeword", "openwakeword"), ("onnxruntime", "onnxruntime"),
    ("sounddevice", "sounddevice"), ("numpy", "numpy"),
]


def survey(tier: str = "small", backend: str = "vulkan") -> list[Need]:
    """Everything VAJREN wants, and whether it is already here."""
    needs: list[Need] = []

    def py_group(key: str, label: str, mods, mb: int) -> None:
        missing = [pip for mod, pip in mods if not _has_module(mod)]
        needs.append(Need(
            key=key, what=label, scope="self", size_mb=mb,
            present=not missing,
            detail=("all present" if not missing else "missing: " + ", ".join(missing)),
            how="pip install " + " ".join(missing) if missing else "",
        ))

    py_group("py_core",   "core Python libraries",        PY_CORE,   120)
    py_group("py_agent",  "the agent loop and router",    PY_AGENT,  350)
    py_group("py_memory", "memory and file indexing",     PY_MEMORY, 60)
    py_group("py_voice",  "the voice stack",              PY_VOICE, 400)

    # --- inference runtime -------------------------------------------------
    needs.append(Need(
        key="llama", scope="self", size_mb=90,
        what=f"the llama.cpp runtime for this machine ({backend} build)",
        present=_llama_binary().exists(),
        detail=f"expected at {_llama_binary()}",
    ))

    # --- weights -----------------------------------------------------------
    have = _models_present()
    tier_mb = {"cpu": 4000, "tiny": 15000, "small": 43000,
               "mid": 60000, "large": 100000, "xlarge": 130000}.get(tier, 43000)
    needs.append(Need(
        key="models", scope="self", size_mb=tier_mb,
        what=f"the model bench for a '{tier}' machine",
        present=len(have) >= 2,
        detail=(f"{len(have)} already here: " + ", ".join(have[:3])) if have
               else "none yet — this is the big download",
    ))

    # --- audio plumbing ----------------------------------------------------
    bundled_ffmpeg = (ROOT / "bin").exists() and any((ROOT / "bin").glob("ffmpeg*"))
    needs.append(Need(
        key="ffmpeg", scope="self", size_mb=80,
        what="ffmpeg, for audio conversion",
        present=_has_cmd("ffmpeg") or bundled_ffmpeg,
        detail="a static build goes in ./bin, not on the system PATH",
    ))

    # --- system-scoped: named, never executed ------------------------------
    needs.extend(_system_needs(backend))
    return needs


def _system_needs(backend: str) -> list[Need]:
    out: list[Need] = []

    if backend == "cuda":
        out.append(Need(
            key="nvidia_driver", scope="system", what="an NVIDIA driver new enough for CUDA 12+",
            present=_has_cmd("nvidia-smi"),
            how="Install the current driver from nvidia.com, then re-run setup.",
            detail="VAJREN will not install a driver. That needs you, once.",
        ))
    if backend in ("vulkan", "hip"):
        vk = (Path(r"C:\Windows\System32\vulkan-1.dll").exists()
              if SYSTEM == "Windows" else _has_cmd("vulkaninfo"))
        out.append(Need(
            key="vulkan_runtime", scope="system", what="a Vulkan runtime",
            present=vk,
            how=("Update your AMD Adrenalin / Intel driver — the Vulkan runtime ships with it."
                 if SYSTEM == "Windows" else
                 "sudo apt install mesa-vulkan-drivers vulkan-tools   (or your distro's equivalent)"),
        ))

    if SYSTEM == "Linux":
        out.append(Need(
            key="portaudio", scope="system", what="PortAudio, for microphone input",
            present=any(Path("/usr/lib").rglob("libportaudio*")),
            how="sudo apt install portaudio19-dev libsndfile1",
        ))
        out.append(Need(
            key="espeak", scope="system", what="a fallback speech voice",
            present=any(_has_cmd(c) for c in ("espeak", "espeak-ng", "spd-say")),
            how="sudo apt install espeak-ng",
        ))

    out.append(Need(
        key="git", scope="system", what="git, for the skill library and backups",
        present=_has_cmd("git"),
        how={"Windows": "winget install Git.Git",
             "Darwin": "brew install git"}.get(SYSTEM, "sudo apt install git"),
    ))
    return out


# --------------------------------------------------------------------------- #
#  Install — self-scoped only. Never elevates. Never touches the system.
# --------------------------------------------------------------------------- #
def pip_install(packages: list[str]) -> bool:
    if not packages:
        return True
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    print(f"    $ {' '.join(cmd[:5])} ...")
    return subprocess.run(cmd).returncode == 0


def plan(needs: list[Need]) -> tuple[list[Need], list[Need]]:
    """Returns (what I will fetch myself, what needs you)."""
    mine = [n for n in needs if n.scope == "self" and not n.present]
    yours = [n for n in needs if n.scope == "system" and not n.present]
    return mine, yours


def describe(needs: list[Need]) -> str:
    mine, yours = plan(needs)
    total_gb = sum(n.size_mb for n in mine) / 1024
    lines = []
    if mine:
        lines.append(f"I need to download about {total_gb:.1f} GB:")
        for n in mine:
            lines.append(f"    - {n.what}  ({n.size_mb/1024:.1f} GB)")
        lines.append("  All of it goes inside my own folder. Nothing else on this")
        lines.append("  machine changes, and deleting the folder undoes all of it.")
    if yours:
        lines.append("")
        lines.append("  These I cannot install for you — they change the whole machine:")
        for n in yours:
            lines.append(f"    - {n.what}")
            lines.append(f"        {n.how}")
    if not mine and not yours:
        lines.append("Everything I need is already here.")
    return "\n  ".join(lines)
