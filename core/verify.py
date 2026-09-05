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
    "run_shell": _shell_ok,
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
