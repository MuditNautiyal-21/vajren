#!/usr/bin/env python3
"""
VAJREN first run.

    python bootstrap.py

Works on Windows, macOS and Linux with nothing but a stock Python 3.10+. It
introduces itself once, learns whose assistant it is, works out what machine it
landed on, and fetches what it needs.

It greets you by voice on the very first run using whatever the OS already has —
SAPI on Windows, `say` on macOS, espeak on Linux — because the good voice is one
of the things it has to install, and an assistant that cannot say hello until
after a 40 GB download is a worse first impression.

Run it again any time. It skips the introduction, re-detects the hardware, and
only fetches what is actually missing — which is what makes moving this thing to
a new machine a one-command operation.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import setup as setup_mod          # noqa: E402
from core.hardware import detect             # noqa: E402
from core.speak import speak                 # noqa: E402

IDENTITY = ROOT / "config" / "identity.json"
NAME = "Vajren"


# --------------------------------------------------------------------------- #
#  small helpers
# --------------------------------------------------------------------------- #
def rule(title: str = "") -> None:
    print(f"\n{'─' * 64}")
    if title:
        print(f"  {title}")
        print("─" * 64)


def ask(prompt: str, default: str = "") -> str:
    d = f" [{default}]" if default else ""
    try:
        return input(f"  {prompt}{d}: ").strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)


def yes(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    a = ask(f"{prompt} ({d})").lower()
    return default if not a else a.startswith("y")


def load_identity() -> dict:
    try:
        return json.loads(IDENTITY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_identity(d: dict) -> None:
    IDENTITY.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY.write_text(json.dumps(d, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  1 — introduction, once and only once
# --------------------------------------------------------------------------- #
def introduce(ident: dict) -> dict:
    if ident.get("greeted"):
        boss = ident.get("boss", "")
        print(f"\n  {NAME} — welcome back{', ' + boss if boss else ''}.")
        return ident

    print(r"""
   __   __     _
   \ \ / /_ _ (_) _ _  ___  _ _
    \ V // _` || || '_|/ -_)| ' \
     \_/ \__,_|/ ||_|  \___||_||_|
             |__/
    """)
    speak(f"Hello. I am {NAME}. This is the first time I have run on this machine.")
    print("  I run on your hardware. Nothing leaves this machine unless you")
    print("  send it somewhere, and I ask before I do anything consequential.\n")

    boss = ""
    while not boss:
        boss = ask("What should I call you?")
    ident.update(boss=boss, greeted=True,
                 first_run=datetime.now().isoformat(timespec="seconds"),
                 assistant_name=NAME)
    save_identity(ident)
    speak(f"Good to meet you, {boss}.")
    return ident


# --------------------------------------------------------------------------- #
#  2 — the machine
# --------------------------------------------------------------------------- #
def survey_machine():
    rule("Working out what I am running on")
    p = detect()
    print(f"  {p.os} · {p.arch} · {p.cores} cores · {p.ram_gb} GB RAM")
    for g in p.gpus:
        tag = "  (integrated — ignoring)" if g.extra.get("integrated") else ""
        print(f"  {g.name} · {g.vram_gb} GB{tag}")
    print(f"\n  Inference backend : {p.backend}")
    print(f"  Why               : {p.backend_reason}")
    print(f"  Model tier        : {p.tier}   (about {p.budget_gb} GB of usable budget)")

    off = [k for k, v in p.features.items() if not v]
    if off:
        print("\n  Switched off on this hardware, deliberately:")
        for k in off:
            print(f"    · {k}")
            print(f"        {p.feature_notes[k]}")
    return p


# --------------------------------------------------------------------------- #
#  3 — keys are optional. This is the part people get backwards.
# --------------------------------------------------------------------------- #
CLOUD_KEYS = [
    ("GROQ_API_KEY",       "Groq",        "console.groq.com  ·  30/min, 1000/day, no card"),
    ("OPENROUTER_API_KEY", "OpenRouter",  "openrouter.ai/keys  ·  many free models"),
    ("NVIDIA_API_KEY",     "NVIDIA NIM",  "build.nvidia.com  ·  phone verify, no card"),
    ("ZAI_API_KEY",        "Z.ai",        "docs.z.ai  ·  GLM-4.7-Flash is free"),
    ("GEMINI_API_KEY",     "Google",      "aistudio.google.com  ·  trains on free-tier data"),
]


def collect_keys(ident: dict) -> None:
    rule("Cloud fallback (optional)")
    env = ROOT / ".env"
    existing = env.read_text(encoding="utf-8") if env.exists() else ""
    have = [k for k, _, _ in CLOUD_KEYS
            if any(l.startswith(k + "=") and l.strip() != k + "=" for l in existing.splitlines())]

    if have:
        print(f"  Already configured: {', '.join(have)}")
        if not yes("Add another?", default=False):
            return
    else:
        print("  I do not need an API key. I run on local models, and that is")
        print("  the default — free, private, and it works with no account at all.")
        print()
        print("  A key only buys a fallback for the handful of things a local")
        print("  model handles badly: long multi-step chains and genuinely novel")
        print("  problems. All of these are free tiers, none need a card except")
        print("  where noted, and your email and files never go to any of them.")
        print()
        if not yes("Add one now?", default=False):
            print("\n  Fine. I will run local-only. Re-run this any time to add one.")
            return

    print()
    for i, (_, label, note) in enumerate(CLOUD_KEYS, 1):
        print(f"    {i}. {label:<12} {note}")
    print()

    lines = existing.splitlines() if existing else []
    for key, label, _ in CLOUD_KEYS:
        val = ask(f"{label} key (blank to skip)")
        if not val:
            continue
        lines = [l for l in lines if not l.startswith(key + "=")]
        lines.append(f"{key}={val}")
        print(f"    saved {label}")
    if lines:
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n  Written to .env — which is gitignored, so it stays here.")
        print("  Move these into Windows Credential Manager later; .env is plaintext.")


# --------------------------------------------------------------------------- #
#  4 — fetch what is missing
# --------------------------------------------------------------------------- #
def install(profile) -> None:
    rule("What I still need")
    needs = setup_mod.survey(tier=profile.tier, backend=profile.backend)
    print("  " + setup_mod.describe(needs))

    mine, yours = setup_mod.plan(needs)
    if not mine:
        print("\n  Nothing to fetch.")
        return

    print()
    if not yes("Go ahead?", default=True):
        print("\n  Left alone. Re-run when you are ready.")
        return

    rule("Fetching")
    for n in mine:
        if n.key.startswith("py_"):
            pkgs = n.how.replace("pip install ", "").split()
            print(f"\n  {n.what}")
            ok = setup_mod.pip_install(pkgs)
            print(f"    {'done' if ok else 'FAILED — see the error above'}")
        else:
            # Runtime, weights and ffmpeg are large and platform-shaped; the
            # scripts already know how to fetch them per platform.
            print(f"\n  {n.what}")
            print(f"    -> run: {_fetch_hint(n.key)}")

    if yours:
        print()
        speak("There are a few things I cannot install for you.")
        for n in yours:
            print(f"    · {n.what}\n        {n.how}")


def _fetch_hint(key: str) -> str:
    win = sys.platform == "win32"
    return {
        "llama":  ".\\scripts\\02-get-runtime.ps1" if win else "./scripts/02-get-runtime.sh",
        "models": ".\\scripts\\03-get-models.ps1" if win else "./scripts/03-get-models.sh",
        "ffmpeg": "python -m core.setup --ffmpeg",
    }.get(key, "see NEXT-STEPS.md")


# --------------------------------------------------------------------------- #
#  5 — where we ended up
# --------------------------------------------------------------------------- #
def finish(ident: dict, profile) -> None:
    rule("Ready")
    boss = ident.get("boss", "")
    needs = setup_mod.survey(tier=profile.tier, backend=profile.backend)
    mine, yours = setup_mod.plan(needs)

    if not mine and not yours:
        speak(f"I am set up and ready{', ' + boss if boss else ''}.")
        print("  Start me with:  python core/main.py")
    else:
        outstanding = len(mine) + len(yours)
        speak(f"Nearly there. {outstanding} thing{'s' if outstanding != 1 else ''} still outstanding.")
        for n in mine + yours:
            print(f"    · {n.what}")
        print("\n  Re-run  python bootstrap.py  once those are done.")

    print(f"\n  What I know about this machine: config/hardware.json")
    print(f"  What I know about you:          config/identity.json")
    print("  Why anything is the way it is:  private/JOURNAL.md\n")


def main() -> None:
    ident = introduce(load_identity())
    profile = survey_machine()
    collect_keys(ident)
    install(profile)
    finish(ident, profile)


if __name__ == "__main__":
    main()
