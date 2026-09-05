# Where this is, and what is next

Everything in [README.md](README.md) under *What it does today* runs and is tested.
The reasoning behind each decision is in a private build journal that is not part of
this repository.

## Checkpoint — 2026-09-05, end of session 5

Last commit on `main`: *Measure the plan loop, then cut what the numbers point at*.
14 suites green (`.\scripts\99-test-all.ps1`, ~7 min). Vajren restarted on this code.

What landed today, in order: hearing fixed (Whisper given the names it must know);
the private journal removed from GitHub; README rewritten to facts; WhatsApp and other
Store apps driven through UI Automation; the new face (left: what it perceives, centre:
particle sphere with live progress and a `why` per step, right: transcript + approval
card); Gemma-4-31B measured at 2.4 tok/s and parked; and the plan loop measured with
`scripts/33-plan-cost.py` — a three-step request went 23.9 s → 18.6 s once files
Vajren wrote itself stopped going through the quarantine model.

**Pick up here.** The per-step floor is now ~2 s of reading + ~2.6 s of writing ~65
tokens of plan JSON. Nothing left in our code moves that; the next lever is streaming
the plan so speech starts before the arguments finish. Second: the cold KV cache after a
model swap costs ~30 s on the next plan and nothing shows the user why.

## Next, in order

1. **Stream the plan.** Emit `spoken_summary` as it is generated so the face speaks
   while the arguments are still being written. Only route under the writing floor.
2. **Always-on.** A Windows service or scheduled task that starts the stack at login,
   restarts it when it dies, and runs `scripts/31-session-audit.py` weekly so the
   score trend is visible. Nothing runs unattended today.
3. **A wake word.** Push-to-talk is the only way in. A real wake-word model
   (openWakeWord or similar, CPU) is the honest fix.
4. **Native dialogs.** `look_at_screen` can read an OK button; the UI Automation hands
   (`app_find` / `app_click`) can now press it — wire the two together for dialogs.
5. **Browser depth.** Scrolling, tabs, and a sign-in flow for Vajren's own Chrome
   profile so LinkedIn-class tasks work end to end.
6. **Real folders.** `writable_roots` is still `workspace/` and `sandbox/`. Widen it
   one folder at a time, deliberately, in `config/policy.yaml`.
7. **Remote.** Tailscale + a Telegram bot, so it can be reached from a phone. Facts
   remembered from remote turns will need a source tag distinct from voice.

## Known rough edges

- Lessons (`memory.record_lesson`) are filed by rule after a turn with a known failure
  shape and read back into the planner prompt. They are not deduplicated or capped yet;
  after a week of use `lessons_for` will need a merge.
- The reflex model writes facts to memory in the background. `scripts/30-memory-report.py`
  is how to see what it wrote; the quality is not yet measured over a week of use.
- `desktop.snapshot()` lists window titles but cannot tell two same-titled windows
  apart ("the one that's logged in") — that still needs the eyes.
- The session audit scores waste and repetition, not whether an action hit the target
  the user *meant*.
- The `writer` alias points at GLM-4.7-Flash. Gemma-4-31B is on disk and configured as
  `vajren-writer-gemma` but is dense, spills to the CPU on a 12 GB card, and measured
  2.4 tok/s — unusable until there is more VRAM.

## Every session ends with

```powershell
.\scripts\99-test-all.ps1          # all suites green, or no commit
.\scripts\commit-session.ps1       # code + journal, verifies nothing private leaked
.\scripts\push-backup.ps1          # code to GitHub + T7; journal to T7 only
```
