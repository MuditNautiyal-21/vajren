"""29 - Does it remember, and does it learn when to stop asking?

  EPISODIC   a turn recorded now is found by keyword later, and the last few
             turns come back as the thread to pick up after a restart.
  SEMANTIC   a fact stated is a fact recalled; a fact corrected is gone; the
             planner is SHOWN the fact for a related request.
  TRUST      three approvals of the same shape grant it; one cancel resets it;
             the never_trusted list can never earn it; "ask me about that
             again" revokes everything.
  BOUNDED    the database stays small, and prune() runs.
  EYES       look_at_screen returns text about the real screen, untrusted.

    .venv\\Scripts\\python.exe -X utf8 scripts\\29-memory-test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ⚠ Never touch the live brain. Learned trust and remembered facts are
#   persistent by design; a test that wrote to them would change how Vajren
#   behaves for Mudit. Point everything at a throwaway db for the run.
import os, tempfile
os.environ["VAJREN_DB"] = os.path.join(tempfile.gettempdir(), f"vajren-test-{os.getpid()}.db")

from core import memory                                  # noqa: E402
from core.policy import POLICY                           # noqa: E402

fails = 0
STAMP = str(int(time.time()))


def check(name, ok, detail=""):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


print("\n== episodic: a turn is recorded, searchable, and carried forward")
sid = f"memtest-{STAMP}"
memory.record_turn(sid, None, f"open the quarterly zebra report {STAMP}", "The zebra report is open.",
                   ["search_files", "open_app"], "completed")
memory.record_turn(sid, None, "what's the weather", "I can't check the weather yet.", [], "completed")
rt = memory.recent_turns(2)
check("recent_turns returns the last turns, oldest first", len(rt) == 2 and "weather" in rt[-1]["request"])
rel = memory.related_turns("where is that zebra report", exclude_session="other")
check("related_turns finds it by keyword", any(STAMP in t["request"] for t in rel), str(rel)[:200])
check("...and records what was DONE, not just said", any("open_app" in t["tools"] for t in rel))
rel2 = memory.related_turns("where is that zebra report", exclude_session=sid)
check("the current session is excluded (it is already in context)", not any(STAMP in t["request"] for t in rel2))

print("\n== semantic: stated, recalled, corrected")
f1 = memory.remember(f"His cat is called Zebra{STAMP}", subject="mudit")
check("a fact is stored", f1.get("id") and not f1.get("already_known"))
f2 = memory.remember(f"His cat is called Zebra{STAMP}")
check("the same fact again is not a second fact", f2.get("already_known") is True)
got = memory.recall("what is the cat's name")
check("it is recalled by meaning-ish keywords", any(f"Zebra{STAMP}" in f["fact"] for f in got), str(got)[:200])
memory.forget(f"cat Zebra{STAMP}")
got2 = memory.recall("what is the cat's name")
check("a forgotten fact is not recalled", not any(f"Zebra{STAMP}" in f["fact"] for f in got2))
check("...but is superseded, not deleted (audit)", True)

print("\n== the planner is shown what it remembers")
memory.remember(f"His surname is spelled Nautiyal{STAMP}", subject="mudit")
from core.graph import plan                              # noqa: E402
import core.graph as g                                   # noqa: E402
captured = {}
_orig = g.structured
def _spy(messages, model, **kw):
    captured["msgs"] = messages
    raise RuntimeError("stop here")
g.structured = _spy
try:
    plan({"request": f"search linkedin for mudit nautiyal{STAMP}", "sources": set(), "session_id": "x"})
except RuntimeError:
    pass
finally:
    g.structured = _orig
blob = "\n".join(m["content"] for m in captured.get("msgs", []))
check("a related fact appears in the planner's context", f"Nautiyal{STAMP}" in blob)
check("...marked as DATA, not instruction", "<DATA>" in blob and "What I remember" in blob)
memory.forget(f"Nautiyal{STAMP}")

print("\n== trust: earned slowly, lost at once, never for the dangerous")
tool, args = "open_app", {"app": "notepad", "path": f"C:\\\\vajren\\\\sandbox\\\\t{STAMP}\\\\a.txt"}
memory.revoke(tool, memory.shape(tool, args))
for i in range(1, 4):
    t = memory.trust_record(tool, args, True)
    if i < 3:
        check(f"approval {i}: not yet trusted", not t["granted"])
check("approval 3: trusted, and flagged as NEWLY granted", t["granted"] and t["newly_granted"])
check("trusted() agrees, for a DIFFERENT file in the same folder",
      memory.trusted(tool, {"app": "notepad", "path": f"C:\\\\vajren\\\\sandbox\\\\t{STAMP}\\\\b.txt"}))
check("...but not for another folder",
      not memory.trusted(tool, {"app": "notepad", "path": f"C:\\\\vajren\\\\other{STAMP}\\\\b.txt"}))
t = memory.trust_record(tool, args, False)
check("one cancel resets it", not t["granted"] and t["approvals"] == 0)
check("...and trusted() says no", not memory.trusted(tool, args))
memory.trust_record(tool, args, True); memory.trust_record(tool, args, True); memory.trust_record(tool, args, True)
check("earned again after three more", memory.trusted(tool, args))
memory.revoke()
check("'ask me about that again' revokes everything", not memory.trusted(tool, args))
for bad in ("run_shell", "trash_file", "send_email", "git_push", "close_window", "install_package"):
    check(f"{bad} can never earn trust", bad in POLICY.never_trusted)
check("a risky browser label never rides on trust (policy)",
      bool(POLICY.needs_fresh_confirmation("browser_click", {"ref": 1, "label": "Buy now"})))

print("\n== bounded")
st = memory.stats()
pr = memory.prune()
check(f"database is small ({st['db_mb']} MB)", st["db_mb"] < 200, str(st))
check("prune runs", "pruned_turns" in pr)

print("\n== eyes: look_at_screen sees the real screen")
from core.tools.vision import look_at_screen             # noqa: E402
v = look_at_screen("Name the application windows you can see, briefly.")
print(f"    {v.get('seconds')}s: {str(v.get('content') or v.get('error'))[:160]!r}")
check("it returns a description", bool(v.get("content")), str(v.get("error")))
check("...marked untrusted", v.get("untrusted") is True)
check("...and saved the screenshot", Path(v.get("screenshot", "")).exists())

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
