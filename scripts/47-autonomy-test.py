"""
47 — the request is the yes, and the folder he named.

Mudit, 2026-09-17: "It should complete the task unless interrupted, be it
writing, opening, closing, trashing, recovering." And: "Give it access to go
beyond the sandbox when given a target folder or something like that."

Two behaviours, and the four ways each could quietly become a hole:

  A  the three undoable tools stop asking
  B  ...but not when forced, and not for a risky label
  C  a write STILL proves its path — autonomy is not a bypass of writable_roots
  D  a folder HE named in the request is writable for that request only
  E  ...and the denylist beats every grant, including one he says out loud

Run:  .venv\\Scripts\\python.exe scripts\\47-autonomy-test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.policy import POLICY, PolicyViolation, Tier            # noqa: E402

fails: list[str] = []


def ok(label: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        fails.append(label)


def tier(tool: str, args: dict | None = None) -> Tier:
    return POLICY.classify(tool, args or {}).tier


def refuses(tool: str, args: dict) -> bool:
    try:
        POLICY.classify(tool, args)
        return False
    except PolicyViolation:
        return True


SANDBOX = str(ROOT / "sandbox" / "a.txt")
HOME = Path.home()

print("\n== A: the three undoable tools do not ask")
POLICY.clear_grants()
ok("policy has the tier switched on", POLICY.request_is_the_yes)
ok("write_file runs",   tier("write_file", {"path": SANDBOX, "content": "x"}) is Tier.AUTO)
ok("trash_file runs",   tier("trash_file", {"path": SANDBOX}) is Tier.AUTO)
ok("close_window runs", tier("close_window", {"title": "Notepad"}) is Tier.AUTO)

print("\n== B: what the tier must never swallow")
ok("close_window force=True still asks",
   tier("close_window", {"title": "Notepad", "force": True}) is Tier.CONFIRM)
ok("a risky label still asks",
   tier("write_file", {"path": SANDBOX, "content": "x", "label": "Pay now"}) is Tier.CONFIRM)
ok("run_shell never rides on it", tier("run_shell", {"command": "dir"}) is Tier.CONFIRM)
ok("app_type never rides on it",  tier("app_type", {"text": "hello"}) is Tier.CONFIRM)
ok("open_path never rides on it", tier("open_path", {"path": SANDBOX}) is Tier.CONFIRM)
ok("an unknown tool still defaults to confirm",
   tier("teleport_file", {"path": SANDBOX}) is Tier.CONFIRM)

print("\n== C: autonomy is NOT a way around writable_roots")
# ⚠ This is the test that matters most. If the tier downgrade is ever moved
#   ABOVE the path check in classify(), `write=(tier is not Tier.AUTO)` becomes
#   write=False and every one of these starts passing silently.
POLICY.clear_grants()
ok("write outside the roots is refused, tier or no tier",
   refuses("write_file", {"path": str(HOME / "Documents" / "nope.txt"), "content": "x"}))
ok("trash outside the roots is refused",
   refuses("trash_file", {"path": str(HOME / "Documents" / "nope.txt")}))
ok("inside the roots is still fine",
   tier("write_file", {"path": SANDBOX, "content": "x"}) is Tier.AUTO)

print("\n== D: the folder he named, for the request he named it in")
POLICY.grant_from_request("save the notes in my Documents folder")
ok("Documents is granted", (HOME / "Documents") in POLICY._granted_roots)
ok("a write there is allowed now",
   tier("write_file", {"path": str(HOME / "Documents" / "notes.txt"), "content": "x"}) is Tier.AUTO)
ok("Downloads is NOT granted by it",
   refuses("write_file", {"path": str(HOME / "Downloads" / "notes.txt"), "content": "x"}))

POLICY.grant_from_request("put it in C:\\vajren\\workspace\\reports")
ok("a literal path in what he said is granted",
   Path("C:/vajren/workspace/reports") in POLICY._granted_roots)

POLICY.grant_from_request("write the summary to C:\\vajren\\workspace\\out.md")
ok("naming a FILE grants its folder, not the file",
   Path("C:/vajren/workspace") in POLICY._granted_roots)

POLICY.grant_from_request("save it on C:\\")
ok("'on C:' grants nothing — a drive is not a target folder",
   POLICY._granted_roots == [])

POLICY.grant_from_request("put it in my home folder " + str(HOME))
ok("the home folder itself is never granted", HOME not in POLICY._granted_roots)

print("\n== E: the grant is his, and the denylist outranks it")
POLICY.clear_grants()
ok("the planner cannot grant itself a folder by putting it in `path`",
   refuses("write_file", {"path": str(HOME / "Desktop" / "x.txt"), "content": "x"}))

POLICY.grant_from_request("write it into C:\\Windows\\System32")
ok("naming C:\\Windows out loud changes nothing",
   refuses("write_file", {"path": "C:\\Windows\\System32\\x.txt", "content": "x"}))

POLICY.grant_from_request("update the policy at C:\\vajren\\config")
ok("he cannot talk it into editing its own permissions",
   refuses("write_file", {"path": "C:\\vajren\\config\\policy.yaml", "content": "x"}))

POLICY.grant_from_request("add it to the journal in C:\\vajren\\private")
ok("the journal stays his",
   refuses("write_file", {"path": "C:\\vajren\\private\\journal.md", "content": "x"}))

POLICY.clear_grants()
ok("clear_grants actually clears",
   refuses("write_file", {"path": str(HOME / "Documents" / "after.txt"), "content": "x"}))

print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("ALL PASS")
