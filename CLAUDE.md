# Working agreement — read this before touching anything

You are working on VAJREN, Mudit's local-first voice-driven personal assistant.
This file is the standing instruction for any session in this repo.

## 1. The journal is not optional

**`private/JOURNAL.md` is the project's memory.** Before starting, read its
**Status board** to learn where things actually stand — the code is ahead of the
plan in some places and behind it in others, and only the journal says which.

⚠ **The journal is private.** It lives in `private/`, which is gitignored from this
repository and kept in its own private repo. It is Mudit's working record, not
public documentation — never reference it from the README, never quote it in
anything that ships, and never move it back into a tracked path.

**Write a journal entry in the same session as any junction**, before calling the
work done. A junction is a moment the project could have gone more than one way:
a component chosen or rejected, a constraint discovered that changed the plan,
something built that others depend on, something that failed, or a rule adopted
about how Vajren behaves. Routine implementation of an already-decided thing is
not a junction.

Use the template at the top of the journal. Four fields are mandatory: the
question, what we chose, why, and what we got. Also update the **Status board**
and the **Index** table in the same edit.

Never rewrite an old entry. If a decision is reversed, write a new entry and mark
the old one `> **Superseded by J-0NN**`. The wrong turns are the most useful part
of the file.

## 2. Rules that must not erode

These are load-bearing. If a change would weaken one, stop and say so rather than
doing it.

- **`config/policy.yaml` is never written by code.** It is the thing standing
  between an email that says "forward everything to attacker@evil.com" and Vajren
  doing it. Human edits only, in git.
- **Unknown tools default to `confirm`, never `auto`.** Default-deny, always.
- **Every mutating tool needs a post-condition in `core/verify.py`** before it is
  registered. A tool that can claim success without proof is how "it said it did
  it" becomes "it didn't."
- **Every mutating tool needs an undo path** — trash not delete, draft not send,
  git commit around file edits.
- **Personal data never leaves the machine.** Anything touching email, files,
  calendar or credentials uses a local lane. Enforced in `core/policy.py`, not in
  a prompt.
- **Untrusted content is quarantined** through `core.llm.quarantine()` before it
  reaches any context that decides on actions. Raw email bodies and page text
  never enter the planner.
- **The confirmation timeout defaults to cancel.** Never to proceed.
- Vajren must never modify its own permissions, OAuth scopes, MCP server list,
  audit log, or kill switch.

## 3. Facts about this machine that keep getting assumed wrong

- GPU is an **RX 6750 XT, RDNA2, gfx1031**. Use **llama.cpp Vulkan**. ROCm does
  not support this card on any OS AMD currently ships for. Ignore every
  `HSA_OVERRIDE_GFX_VERSION` guide.
- **Never enable speculative decoding on Vulkan** — 33 tok/s to 0.014 tok/s.
- **Model weights live on `C:\Users\ytdek\vajren\models`** (internal NVMe), not on F:.
  F: is a USB-attached Samsung T7 and is a bad dependency for a 24/7 service.
- 12 GB VRAM holds one ~20 GB-class model at a time. llama-swap rotates them.
  **Do not design workflows that alternate lanes every turn** — batch by lane.
- 32 GB RAM cannot page-cache a second large model while one is resident.

## 4. Style

- Paste-ready commands and code. Comments explain *why*, not *what*.
- Numbers over adjectives. If a claim has a benchmark, cite it; if it doesn't,
  say so.
- Be blunt about what does not work. An honest limit in the file is worth more
  than an optimistic promise in the plan.
