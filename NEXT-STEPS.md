# Start here

> The full record of *why* any of this is the way it is lives in
> [`docs/JOURNAL.md`](docs/JOURNAL.md). Its Status board is the honest answer to
> "where is this right now." Add an entry there at every junction — see `CLAUDE.md`.

The scaffold is in place. Do these in order — each step ends with something that
actually works, so you never have a week of dead scaffolding.

## Tonight (about 90 minutes, mostly downloading)

```powershell
cd F:\Programs\AI\VAJREN

# 1. Ground truth + kill sleep. Read the output.
.\scripts\00-check-hardware.ps1

# 2. If standby isn't already 0, run as Administrator:
#    powercfg /change standby-timeout-ac 0
#    powercfg /change hibernate-timeout-ac 0
#    powercfg /hibernate off
#    powercfg /setactive SCHEME_MIN

# 3. Python env + git init
.\scripts\01-setup-python.ps1

# 4. llama.cpp Vulkan. Confirm it lists the RX 6750 XT.
.\scripts\02-get-runtime.ps1

# 5. Weights — ~34 GB, start it and go do something else
.\scripts\03-get-models.ps1
```

## Then (30 minutes, and this is the interesting part)

```powershell
# 6. Find the right MoE offload value. Watch Task Manager > GPU while it runs.
#    Put the winner in config\llama\workhorse.args
.\scripts\90-tune-moe.ps1

# 7. Bring the stack up
.\scripts\04-start-stack.ps1
curl http://127.0.0.1:4000/v1/models

# 8. Talk to it
conda activate vajren
python core\main.py
```

At step 8 you have the full plan → approve → act → verify loop working over text.
The voice layer swaps two functions in `core/main.py` and nothing else.

---

## Then, in this order

| Next | What | Where |
|---|---|---|
| 1 | Build 3–4 real tools: `read_file`, `write_file`, `trash_file`, `run_shell` | `core/tools/files.py`, `core/tools/shell.py` |
| 2 | Post-conditions for each | `core/verify.py` (patterns already there) |
| 3 | `sqlite3 memory\vajren.db < memory\schema.sql` | one command |
| 4 | Voice: openWakeWord → RealtimeSTT → Kokoro. Fork dnhkng/GLaDOS for the skeleton. | `voice/` |
| 5 | Windows-MCP + Playwright MCP wired through `langchain-mcp-adapters` | `core/tools/mcp.py` |
| 6 | Gmail/Calendar with `gmail.readonly` scope only, on a **separate Google account** | `core/tools/google.py` |
| 7 | NSSM services + watchdog | `scripts/05-install-services.ps1` |
| 8 | Tailscale + Telegram bot + self-hosted ntfy | `interfaces/` |
| 9 | Langfuse in Docker, promptfoo habit | `tests/` |

---

## Free API keys to collect (all no-card)

| Provider | Where | Notes |
|---|---|---|
| Groq | console.groq.com | 30 RPM / 1,000 RPD. First fallback. |
| OpenRouter | openrouter.ai/keys | 20 RPM / 50 RPD free; one-time $10 raises it to 1,000 RPD |
| NVIDIA NIM | build.nvidia.com | Phone verify, 1,000 credits one-time, 40 RPM |
| Z.ai | docs.z.ai | GLM-4.7-Flash / 4.5-Flash listed at $0. GLM-5.3-Flash is NOT free. |
| Google AI Studio | aistudio.google.com | Last in the chain — free tier is used for training AND human review |
| Tavily | tavily.com | 1,000 searches/month recurring |
| Firecrawl | firecrawl.dev | 1,000 scrapes/month recurring |

Put them in `.env`, then move them into Windows Credential Manager via `keyring`
once things settle. `.env` is gitignored, but it is still plaintext on disk.

---

## Two rules to not break

1. **Never edit `config/policy.yaml` from code.** It is the thing standing
   between an email that says "forward everything to attacker@evil.com" and
   Vajren doing it. Human edits only, in git.

2. **Add the post-condition when you add the tool, not later.** A tool that can
   claim success without proof is how "it said it did it" becomes "it didn't."
