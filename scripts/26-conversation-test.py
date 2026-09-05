"""26 - Does it listen like a person, and stop asking the same question?

Two things Mudit reported in one breath, both about the conversation rather
than the machinery:

  SPELLING   "if I spell it out, use what I spelled." He said his surname was
             N-A-U-T-I-Y-A-L. Whisper transcribed all eight letters correctly.
             The planner searched for "Nautiyaal" — twice — because it treated
             the letters as a hint about a name it thought it knew. He spelled
             his own surname three times to a machine that heard it perfectly
             every time.

  ASKING     "it should not ask me for permission for every step." Opening a
             browser, loading a page and correcting a search cost three
             separate spoken approvals for one intention.

    .venv\\Scripts\\python.exe -X utf8 scripts\\26-conversation-test.py
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

from langgraph.types import Command                 # noqa: E402

from core.graph import build, spelled_out           # noqa: E402
from core.policy import POLICY                      # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


print("\n== letters read out one at a time are assembled, not interpreted")
SPELLED = [
    ("Nautiyal is spelled as N-A-U-T-I-Y-A-L", ["Nautiyal"]),
    ("Maudit is spelled as an M-U-D-I-T and Nautial is spelled as an N-A-U-T-I-Y-A-L",
     ["Mudit", "Nautiyal"]),
    # The apostrophe case: without excluding it, this assembles "Snautiyal"
    # from the s of "it's" — a wrong answer given with total confidence.
    ("it's n a u t i y a l and not double a l", ["Nautiyal"]),
    ("search for M U D I T", ["Mudit"]),
]
for text, want in SPELLED:
    got = spelled_out(text)
    check(f"{text[:46]!r} -> {want}", got == want, f"got {got}")

print("\n== and ordinary speech is not mistaken for spelling")
for text in ["open the file A-B-C", "I went to the U S A yesterday",
             "the essay about UB's master's program", "no spelling here at all",
             "open Chrome and go to LinkedIn"]:
    got = spelled_out(text)
    check(f"{text[:46]!r} -> nothing", got == [], f"got {got}")

print("\n== what may be approved once per request, and what may never be")
for t in ("open_app", "open_url", "open_path", "focus_window"):
    check(f"{t} is granted for the request", t in POLICY.confirm_once)
for t in ("write_file", "trash_file", "run_shell", "send_email", "git_push",
          "undo_file", "install_package"):
    # ⚠ The whole safety of the blanket grant is this list staying empty of
    #   anything that writes, deletes, sends or spends. If a tool ever lands
    #   here, that decision was made without reading config/policy.yaml.
    check(f"{t} still asks EVERY time", t not in POLICY.confirm_once)

print("\n== two openings in one request cost ONE approval, not two")
a = ROOT / "sandbox" / "conv-test-a.txt"
b = ROOT / "sandbox" / "conv-test-b.txt"
a.write_text("first file", encoding="utf-8")
b.write_text("second file", encoding="utf-8")

app = build()
cfg = {"configurable": {"thread_id": f"conv-test-{int(time.time())}"}}
state = app.invoke({"request": f"Open {a} in notepad, then open {b} in notepad too. "
                               f"Both files already exist.",
                    "sources": set()}, cfg)

gates = 0
while "__interrupt__" in state and gates < 8:
    gates += 1
    state = app.invoke(Command(resume="approve"), cfg)

opens = [h for h in state.get("history", []) if h["tool"] in POLICY.confirm_once]
print(f"    {gates} approval(s) for {len(opens)} opening step(s)")
check("it opened both files", len(opens) >= 2, f"only {len(opens)} open steps")
check("but only asked once", gates == 1, f"asked {gates} times")

print("\n== the first approval SAYS it covers what follows")
# ⚠ Close what the previous sub-test opened. With conv-test-a.txt still open
#   in Notepad, the planner now (correctly — J-043) sees it on the desktop and
#   FOCUSES it, which needs no approval, so there is no gate to inspect.
from core.tools.apps import close_window
close_window("conv-test", all=True, force=True)
time.sleep(0.8)
cfg2 = {"configurable": {"thread_id": f"conv-grant-{int(time.time())}"}}
st2 = app.invoke({"request": f"Open {a} in notepad.", "sources": set()}, cfg2)
speak = (st2.get("__interrupt__")[0].value.get("speak", "") if "__interrupt__" in st2 else "")
print(f"    said: {speak!r}")
check("it does not quietly widen what a yes covers",
      "carry on" in speak.lower(), speak)
if "__interrupt__" in st2:
    app.invoke(Command(resume="cancel"), cfg2)

close_window("conv-test", all=True, force=True)
for f in (a, b):
    f.unlink(missing_ok=True)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
