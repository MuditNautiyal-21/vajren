"""
Deterministic post-conditions. No LLM involved.

The rule: if a tool claims it did something, code proves it. If code cannot prove
it, the tool does not belong in the `auto` tier.

Add one entry here for every tool you add. A tool with no post-condition is a
tool that can lie about succeeding.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

Check = Callable[[dict, dict], bool]


def _file_written(action: dict, result: dict) -> bool:
    p = Path(action["args"]["path"])
    if not p.exists():
        return False
    if "expected_sha256" in result:
        return hashlib.sha256(p.read_bytes()).hexdigest() == result["expected_sha256"]
    return p.stat().st_size > 0


def _file_trashed(action: dict, result: dict) -> bool:
    # The original must be gone AND the trashed copy must be where the undo_ref
    # says it is. "It's gone" alone is what a delete looks like too.
    ref = result.get("undo_ref", "")
    if not ref.startswith("trash|"):
        return False
    store = Path(ref.split("|", 2)[1])
    return not Path(action["args"]["path"]).exists() and store.is_file()


def _file_restored(action: dict, result: dict) -> bool:
    return bool(result.get("restored")) and Path(result["restored"]).exists() \
        if result.get("undone") == "trash" else bool(result.get("restored"))


def _shell_ok(action: dict, result: dict) -> bool:
    # returncode 0 is necessary, not sufficient. A command that hung and was
    # killed is a failure whatever code the shell reports, and a command that
    # promised to produce a path has to have produced it.
    if result.get("timed_out") or result.get("returncode") != 0:
        return False
    if result.get("expect_path"):
        return Path(result["expect_path"]).exists()
    return True


def _launched(action: dict, result: dict) -> bool:
    # ⚠ The obvious rule — "a windowed program that exited immediately failed" —
    #   is wrong on Windows. Chrome, Edge and Office are single-instance: the
    #   process you start forwards the request to the running one and exits 0.
    #   Under the obvious rule, Chrome opened on screen and was marked failed,
    #   and the planner spent three more approvals reopening it. `launched` is
    #   "still alive OR exited cleanly"; a non-zero exit is still a failure.
    return bool(result.get("opened")) or bool(result.get("launched")) \
        or bool(result.get("running")) or bool(result.get("focused"))


def _closed(action: dict, result: dict) -> bool:
    # Closed means the windows are GONE, re-read from the desktop, not that a
    # message was posted. One survivor (a Save? dialog) is a failure to report.
    return result.get("count", 0) > 0 and not result.get("still_open")


def _page_opened(action: dict, result: dict) -> bool:
    # The page we are on must be the one asked for (allowing redirects to the
    # same site — youtube.com -> www.youtube.com/... is not a failure).
    want = str(action["args"].get("url", "")).lower()
    got = str(result.get("url", "")).lower()
    if not got or got.startswith("chrome-error://"):
        return False
    import re
    host = lambda u: re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
    return host(want) == host(got) or host(want) in got


def _clicked(action: dict, result: dict) -> bool:
    # The click landed on the thing that was named. Whether the page then
    # changed is the page's business; a click on "Play" navigates nowhere.
    return bool(result.get("clicked")) and result.get("ref") == action["args"].get("ref")


def _typed(action: dict, result: dict) -> bool:
    want = str(action["args"].get("text", ""))
    return str(result.get("value", "")).strip() == want.strip() or bool(result.get("navigated"))


def _went_back(action: dict, result: dict) -> bool:
    return bool(result.get("url"))


def _focused(action: dict, result: dict) -> bool:
    # ⚠ Its OWN key, not _launched's. focus_window used to share that check,
    #   which accepted `focused` as true — and `focused` was whatever
    #   SetForegroundWindow returned, which is TRUE even when Windows merely
    #   flashes the taskbar button. Four verified successes, one window that
    #   never moved. The tool now reads GetForegroundWindow back, and this
    #   asserts that reading and nothing else.
    return result.get("focused") is True


def _draft_exists(action: dict, result: dict) -> bool:
    return bool(result.get("draft_id"))


def _email_sent(action: dict, result: dict) -> bool:
    # must be findable in Sent, not merely "the API returned 200"
    return bool(result.get("message_id")) and result.get("in_sent") is True


def _event_exists(action: dict, result: dict) -> bool:
    return bool(result.get("event_id")) and result.get("readback_ok") is True


def _command_ok(action: dict, result: dict) -> bool:
    return result.get("returncode") == 0


def _committed(action: dict, result: dict) -> bool:
    return bool(result.get("sha")) and result.get("tree_clean") is True


def _searched(action: dict, result: dict) -> bool:
    # Finding nothing is a valid answer to "where is it?". What must be true is
    # that the search RAN — it reports which roots it looked in. Failing this on
    # an empty result would make the planner retry a lookup that was correct.
    return isinstance(result.get("matches"), list) and bool(result.get("roots"))


POSTCONDITIONS: dict[str, Check] = {
    "write_file": _file_written,
    "move_file": _file_written,
    "trash_file": _file_trashed,
    "undo_file": _file_restored,
    "draft_email": _draft_exists,
    "send_email": _email_sent,
    "reply_email": _email_sent,
    "create_calendar_event": _event_exists,
    "update_calendar_event": _event_exists,
    "search_files": _searched,
    "run_shell": _shell_ok,
    "open_app": _launched,
    "focus_window": _focused,
    "open_url": _launched,
    "close_window": _closed,
    "browser_open": _page_opened,
    "browser_click": _clicked,
    "browser_type": _typed,
    "browser_back": _went_back,
    "open_path": _launched,
    "run_python": _command_ok,
    "run_tests": _command_ok,
    "git_commit": _committed,
}


def check_postcondition(action: dict, result: dict) -> bool:
    if result.get("error"):
        return False
    check = POSTCONDITIONS.get(action["tool"])
    if check is None:
        # Read-only tools need no proof; anything else without a check is a bug.
        return not result.get("mutating", False)
    try:
        return bool(check(action, result))
    except Exception:
        return False
