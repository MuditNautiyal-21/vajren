# Where this is, and what is next

Everything in [README.md](README.md) under *What it does today* runs and is tested.
The reasoning behind each decision is in a private build journal that is not part of
this repository.

## Next, in order

1. **Always-on.** A Windows service or scheduled task that starts the stack at login,
   restarts it when it dies, and runs `scripts/31-session-audit.py` weekly so the
   score trend is visible. Nothing runs unattended today.
2. **A wake word.** Push-to-talk is the only way in. Whisper is given "Vajren" as a
   spelling hint (`config/voice-names.txt`); a real wake-word model (openWakeWord or
   similar, CPU) is the honest fix.
3. **Native dialogs.** `look_at_screen` can read an OK button; nothing presses it.
   UI Automation (pywinauto is already installed) is the reliable layer for that.
4. **Browser depth.** Scrolling, tabs, and a sign-in flow for Vajren's own Chrome
   profile so LinkedIn-class tasks work end to end.
5. **Real folders.** `writable_roots` is still `workspace/` and `sandbox/`. Widen it
   one folder at a time, deliberately, in `config/policy.yaml`.
6. **Remote.** Tailscale + a Telegram bot, so it can be reached from a phone. Facts
   remembered from remote turns will need a source tag distinct from voice.

## Known rough edges

- The reflex model writes facts to memory in the background. `scripts/30-memory-report.py`
  is how to see what it wrote; the quality is not yet measured over a week of use.
- `desktop.snapshot()` lists window titles but cannot tell two same-titled windows
  apart ("the one that's logged in") — that still needs the eyes.
- The session audit scores waste and repetition, not whether an action hit the target
  the user *meant*.
- A dedicated writer model is configured in `llama-swap.yaml` but not downloaded.

## Every session ends with

```powershell
.\scripts\99-test-all.ps1          # all suites green, or no commit
.\scripts\commit-session.ps1       # code + journal, verifies nothing private leaked
.\scripts\push-backup.ps1          # code to GitHub + T7; journal to T7 only
```
