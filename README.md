# VAJREN

A local-first, voice-driven autonomous assistant. Runs on this machine, costs $0/month,
states its plan out loud and executes only on spoken approval.

**Start with [`docs/JOURNAL.md`](docs/JOURNAL.md)** — the running record of every
decision, why it was made, what it cost and what it bought. Its Status board is
the honest answer to "where is this right now."

`CLAUDE.md` holds the working agreement for any AI session in this repo.

## Target machine

| | |
|---|---|
| CPU | AMD Ryzen 5 7600X (6c/12t) |
| RAM | 32 GB |
| GPU | Radeon RX 6750 XT — 12 GB VRAM, RDNA2, gfx1031 |
| OS | Windows 10 Pro 19045 |
| Backend | llama.cpp **Vulkan** (NOT ROCm — AMD dropped RDNA2 + Win10) |

## Layout

```
VAJREN/
  config/          policy, router, llama-swap, litellm, models, voice
  core/            orchestrator: graph, policy gate, router, llm client, style, verify
  voice/           wake word, STT, TTS, barge-in, approval dialogue
  memory/          schema.sql + sqlite databases (gitignored)
  skills/          git-versioned SKILL.md library, one folder per skill
  scripts/         setup and run scripts (PowerShell)
  logs/            service + audit logs (gitignored)
  workspace/       scratch space the agent may write to
  sandbox/         throwaway area for code the agent wrote
  tests/           promptfoo regression suite
  docs/            JOURNAL.md (the record), DESIGN-TOOLKIT.md
```

## The one habit that keeps this legible

Every junction gets a journal entry, written in the same session it happened —
what we were facing, what we rejected, what we chose, why, what it cost, what we
got. Not at the end of the week: the reasoning evaporates within hours and what
survives is a bare conclusion nobody can re-argue.

## Quick start

```powershell
cd F:\Programs\AI\VAJREN
.\scripts\00-check-hardware.ps1     # confirm Vulkan + VRAM + free disk
.\scripts\01-setup-python.ps1       # conda env + deps
.\scripts\02-get-runtime.ps1        # llama.cpp Vulkan build
.\scripts\03-get-models.ps1         # download GGUF weights
.\scripts\04-start-stack.ps1        # llama-server tiers + LiteLLM
```

## Non-negotiables

1. Personal data (email, files, calendar, credentials) **never** leaves this machine.
   Enforced in code (`core/policy.py`), not in a prompt.
2. Risky actions speak their plan and wait for an explicit spoken confirmation phrase.
   Silence means cancel, never proceed.
3. Every destructive action has an undo. Trash, not delete. Draft, not send.
4. Every tool call is schema-constrained and every "done" is verified by code.
5. The agent never modifies its own approval gate, scopes, or MCP server list.
