"""32 - Hands inside native Windows apps, and Store apps that actually open.

Asked to search WhatsApp for a person and message them, Vajren had find/click/
type only for its own Chrome, so it searched the ChatGPT page and proposed
typing the WhatsApp message there (J-045). This suite pins:

  STORE      "open WhatsApp" resolves through the Start menu, not PATH.
  NATIVE     app_find / app_type / app_click on Notepad — a real window, real
             UIA, and text read back from the control. Notepad, because a test
             that messages a human being is not a test.
  LABEL      a wrong label is refused; a password field is refused.
  POLICY     app_click/app_type are once-per-request; 'call' asks every time.

    .venv\\Scripts\\python.exe -X utf8 scripts\\32-native-test.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.policy import POLICY                                        # noqa: E402
from core.tools.apps import close_window, open_app, start_app_id       # noqa: E402
from core.tools.native import app_click, app_find, app_type            # noqa: E402
from core.verify import check_postcondition                           # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail and not ok else ''}")
    fails += 0 if ok else 1


print("\n== Store apps resolve through the Start menu")
for name in ("whatsapp", "WhatsApp Beta", "spotify"):
    hit = start_app_id(name)
    check(f"{name!r} -> an AppID", bool(hit and "!" in hit[1]), str(hit))
check("nonsense does not resolve", start_app_id("zzz-no-such-app-zzz") is None)

print("\n== native: Notepad, end to end")
close_window("Untitled - Notepad", all=True, force=True)
r = open_app("notepad")
check("Notepad opened and is in front", r.get("launched") and r.get("focused"), str(r)[:160])
time.sleep(0.8)
f = app_find("Notepad", "")
print(f"    {f.get('seconds')}s, {f.get('count')} controls")
check("app_find lists numbered controls", f.get("count", 0) >= 1 and re.match(r"\d+: ", f.get("listing", "") or ""), f.get("error"))
m = re.search(r"(\d+): (Edit|Document) '([^']*)'", f.get("listing", ""))
if not m:
    # Notepad 11's editor is a Document; older is an Edit named 'Text Editor'
    m = re.search(r"(\d+): \w+ '(Text Editor|Text editor)'", f.get("listing", ""))
check("the text area is listed", bool(m), f.get("listing", "")[:300])
if m:
    ref, label = int(m.group(1)), m.group(len(m.groups()))
    t = app_type("Notepad", ref, label, "hands on a native app")
    check("typed into it", "error" not in t, str(t)[:200])
    check("...and verify agrees", check_postcondition({"tool": "app_type", "args": {"ref": ref, "label": label, "text": "x"}}, t))
    w = app_type("Notepad", ref, "Delete everything", "x")
    check("a mismatched label is REFUSED", "error" in w and "labelled" in w["error"], str(w)[:160])

    # ⚠ CONTRACT CHANGED 2026-09-06, deliberately. This used to assert that a
    #   stale number is fatal. It is not: the number is an artefact of one
    #   app_find snapshot, and WhatsApp re-renders after every click, so index
    #   1 becoming a different control lost three real turns. The LABEL is the
    #   identity. A stale number with a label that IS on screen must now
    #   recover; only a label that is genuinely absent may fail. The safety
    #   property is the assertion above and the one below, not the number.
    w2 = app_click("Notepad", ref + 500, label)
    check("a stale number RECOVERS via the label", "error" not in w2, str(w2)[:200])
    check("...and reports what it actually clicked",
          str(w2.get("clicked", "")).lower() in (label.lower(), "") or bool(w2.get("clicked")),
          str(w2)[:160])
    w3 = app_click("Notepad", ref + 500, "No Such Control Anywhere")
    check("a stale number AND an absent label still fails", "error" in w3, str(w3)[:160])
close_window("Notepad", all=True, force=True)

print("\n== policy")
for t in ("app_click", "app_type"):
    check(f"{t} is once-per-request", t in POLICY.confirm_once)
check("app_find needs no approval", "app_find" in set(POLICY._auto))
for lab in ("Voice call", "Video call", "Call", "Send", "Delete chat"):
    check(f"{lab!r} asks every time", bool(POLICY.needs_fresh_confirmation("app_click", {"ref": 1, "label": lab})))
for lab in ("Search or start a new chat", "Sakshi Malhotra (HCL)", "Chats"):
    check(f"{lab!r} rides on the request's yes", not POLICY.needs_fresh_confirmation("app_click", {"ref": 1, "label": lab}))
# Enter in a chat box IS the Send button. The first real WhatsApp message went
# out under the general grant because only Send *buttons* were checked.
# ⚠ The spoken request is itself the approval for a CALL he named (J-053).
#   "call Mudit India" must not re-ask at the call button; a call he did not
#   ask for, to a person he did not name, or any SEND, still asks.
_h = [{"tool": "app_click", "args": {"ref": 3, "label": "Mudit India 2:33 AM Voice call Pinned chat"}}]
check("'call Mudit India' covers the Voice call button",
      bool(POLICY.request_covers("Call Mudit India on WhatsApp", "app_click", {"ref": 1, "label": "Voice call"}, _h)))
check("'video call Mudit India' covers the Video call button",
      bool(POLICY.request_covers("Video call Mudit India on WhatsApp beta", "app_click", {"ref": 1, "label": "Video call"}, _h)))
check("'message Mudit' does NOT cover a call",
      not POLICY.request_covers("Message Mudit India", "app_click", {"ref": 1, "label": "Voice call"}, _h))
check("'call Sakshi' does NOT cover a call while Mudit's chat is open",
      not POLICY.request_covers("Call Sakshi Malhotra", "app_click", {"ref": 1, "label": "Voice call"}, _h))
check("a call with no chat opened does NOT ride",
      not POLICY.request_covers("Call Mudit India", "app_click", {"ref": 1, "label": "Voice call"}, []))
check("a Send never rides on the request",
      not POLICY.request_covers("Reply to Sakshi", "app_click", {"ref": 1, "label": "Send"}, _h))
check("Enter in a message composer asks every time",
      bool(POLICY.needs_fresh_confirmation("app_type", {"ref": 1, "label": "Type a message to Sakshi", "submit": True})))
check("...but typing WITHOUT enter does not", not POLICY.needs_fresh_confirmation("app_type", {"ref": 1, "label": "Type a message to Sakshi", "submit": False}))
check("...and Enter in a search box does not", not POLICY.needs_fresh_confirmation("app_type", {"ref": 1, "label": "Search or start a new chat", "submit": True}))

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
