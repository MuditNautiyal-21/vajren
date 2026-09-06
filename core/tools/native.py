"""
Hands for native Windows apps — WhatsApp, Spotify, Settings, dialogs.

WHY: asked to search WhatsApp for a person and message them, Vajren had find/
click/type only for its OWN Chrome. So it ran browser_find, which searched the
ChatGPT page it had open, found a textarea labelled "Chat with ChatGPT", and
proposed typing the WhatsApp message into it. Mudit cancelled. A missing tool
became a confident wrong plan, for the third time in one day.

HOW: Windows UI Automation, through pywinauto's `uia` backend. The same three
verbs as the browser — find numbered controls, click one by number+label, type
into one by number+label — against one named window. Numbers come from
app_find and die when the next app_find runs; the label is re-read from the
control before anything is pressed, so the planner cannot describe its way
past the gate.

⚠ BOUNDED WALK. WhatsApp Beta's full UIA tree is 34,000 elements and takes
  23 s to enumerate. app_find walks depth-first with a cap on depth and count,
  keeps only control types a person could act on, and de-duplicates by
  (type, name, rectangle), because that tree repeats every control ~76 times.

What comes back is UNTRUSTED like a page: control names are written by whoever
wrote the app, and a chat message is a control name.
"""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from core.tools import tool

ACTIONABLE = {"Edit", "Button", "ListItem", "TabItem", "MenuItem", "CheckBox",
              "RadioButton", "ComboBox", "Hyperlink", "TreeItem", "DataItem",
              "SplitButton", "Slider"}
MAX_ITEMS = 80
_refs: dict[str, list] = {}          # window title -> [wrapper, ...] from the last find


def _window(title: str):
    from pywinauto import Desktop
    import re
    d = Desktop(backend="uia")
    pat = ".*" + re.escape(title.strip()) + ".*"
    wins = d.windows(title_re=pat, visible_only=True)
    if not wins:
        raise LookupError(f"no open window whose title contains {title!r}")
    # Prefer the largest — a tooltip or popup can match the same title.
    wins.sort(key=lambda w: -(w.rectangle().width() * w.rectangle().height()))
    return wins[0]


def _label(el) -> str:
    try:
        n = (el.element_info.name or "").strip()
        if not n:
            n = (el.element_info.automation_id or "").strip()
        return n[:80]
    except Exception:                                              # noqa: BLE001
        return ""


def _walk(root, query: str) -> list[tuple[str, str, object]]:
    """[(kind, label, wrapper)] — one UIA FindAll per control type, de-duplicated.

    ⚠ Not children()-recursion. On WhatsApp Beta (a hosted WebView) children()
      of the content pane returns NOTHING while a subtree FindAll returns
      34,000 elements — so a depth-first walk saw four title-bar buttons and
      stopped. FindAll by control type is 1.5–3 s per type and the duplicates
      (each control appears ~76 times) collapse under (type, name, rect).
    """
    q = [w for w in (query or "").lower().split() if w]
    out: list[tuple[str, str, object]] = []
    seen: set[tuple] = set()
    # ⚠ MEASURED: WhatsApp Beta exposes 82 identical Document nodes, each
    #   holding a full copy of the UI. A FindAll from the window walks all 82
    #   — 14,476 DataItems, 30 s. The same FindAll inside the FIRST Document
    #   is 184 DataItems in 0.1 s. So: search each top-level Document once,
    #   plus the window's own chrome (title bar, dialogs) outside any Document.
    roots = [root]
    try:
        docs = root.descendants(control_type="Document")
        if docs:
            firsts, seen_rect = [], set()
            for d in docs:
                r = d.element_info.rectangle
                k = (r.left, r.top, r.right, r.bottom)
                if k not in seen_rect:
                    seen_rect.add(k)
                    firsts.append(d)
            roots = firsts[:4]
    except Exception:                                              # noqa: BLE001
        pass
    for kind in ("Edit", "Button", "ListItem", "DataItem", "TabItem", "MenuItem",
                 "CheckBox", "Hyperlink", "ComboBox", "TreeItem"):
        els = []
        for rt in roots:
            try:
                els += rt.descendants(control_type=kind)
            except Exception:                                      # noqa: BLE001
                continue
        for el in els:
            try:
                info = el.element_info
                r = info.rectangle
                if r.right - r.left <= 1 or r.bottom - r.top <= 1:
                    continue
                label = _label(el)
                if not label:
                    continue
                key = (kind, label, r.left, r.top, r.right, r.bottom)
            except Exception:                                      # noqa: BLE001
                continue
            if key in seen:
                continue
            seen.add(key)
            if not q or all(w in (label + " " + kind).lower() for w in q):
                out.append((kind, label, el))
                if len(out) >= MAX_ITEMS:
                    return out
    return out


def _listing(items) -> str:
    return "\n".join(f"{i + 1}: {kind} {label!r}" for i, (kind, label, _) in enumerate(items))


class AppFind(BaseModel):
    window: str = Field(description="part of the window's title, e.g. 'WhatsApp'")
    query: str = Field(default="", description="words that should appear in the control's "
                                              "label or type; blank for everything")


@tool("app_find", AppFind)
def app_find(window: str, query: str = "") -> dict:
    """Numbered list of buttons, fields and items in a native window. UNTRUSTED."""
    t0 = time.perf_counter()
    try:
        win = _window(window)
        items = _walk(win, query)
    except Exception as e:                                         # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "untrusted": True}
    _refs[window.strip().lower()] = [el for _, _, el in items]
    return {"window": win.window_text(), "count": len(items), "listing": _listing(items),
            "seconds": round(time.perf_counter() - t0, 1), "untrusted": True}


def _bring_front(window: str) -> None:
    """Raise the window before clicking in it.

    ⚠ UIA clicks are coordinates. With Chrome maximised over WhatsApp, every
      click on "Sakshi Malhotra" landed on Chrome, the tool reported success,
      and the chat never opened — the vision model was what noticed WhatsApp
      was not even visible. Nothing is pressed until the window is in front.
    """
    try:
        from core.tools.apps import _raise
        win = _window(window)
        _raise(win.handle)
        time.sleep(0.2)
    except Exception:                                              # noqa: BLE001
        pass


def _same_label(want: str, have: str) -> bool:
    a, b = (want or "").strip().lower(), (have or "").strip().lower()
    return bool(a and b and (a in b or b in a))


def _locate(window: str, ref: int, label: str):
    """The control the planner meant — found by LABEL, with the number as a hint.

    ⚠ MEASURED FAILURE, three times across two sessions on 2026-09-05.
      `app_find` caches live UIA wrappers in `_refs`. WhatsApp re-renders after
      every click, so by the time `app_type` fires, index 1 is a different
      control and `_label()` returns '_r_a_'. The old code raised
      "item 1 is labelled '_r_a_', not 'Search or start a new chat'" and the
      whole request died — refusing to click the wrong thing, which is right,
      but ending the task to do it, which is not.

      The NUMBER is an artefact of one snapshot. The LABEL is what Mudit said
      and what the planner meant. So: try the number; if the label underneath
      has changed, walk the window again and find the label. Fail only when the
      label genuinely is not on screen — the one case where refusing is the
      correct answer, and the case the guard was written for.

      Costs one extra walk (~2 s) on a miss. The old behaviour cost a re-plan
      (~4.5 s) plus a fresh app_find (~2 s) AND usually lost the turn anyway.
    """
    key = window.strip().lower()
    refs = _refs.get(key) or []
    if not refs and not label:
        raise LookupError("call app_find on that window first — the numbers come from it")

    if 1 <= int(ref) <= len(refs):
        el = refs[int(ref) - 1]
        actual = _label(el)                    # "" if the wrapper has gone stale
        if not label or _same_label(label, actual):
            return el, actual

    if not label:
        raise LookupError(f"nothing numbered {ref}; call app_find on that window again")

    # The snapshot is stale. Re-walk and match on what the label says.
    items = _walk(_window(window), "")
    _refs[key] = [el for _, _, el in items]
    want = label.strip().lower()
    for _, cand, el in items:                  # exact first, so 'Chats' never
        if cand.strip().lower() == want:       # steals a click meant for 'Chat'
            return el, cand
    for _, cand, el in items:
        if _same_label(label, cand):
            return el, cand
    raise LookupError(f"nothing labelled {label!r} is on screen in {window!r}. "
                      f"Call app_find again and use a label you can see.")


class AppClick(BaseModel):
    window: str = Field(description="part of the window's title")
    ref: int = Field(description="the number from app_find")
    label: str = Field(description="the label shown next to that number, repeated exactly")


@tool("app_click", AppClick, mutating=True)
def app_click(window: str, ref: int, label: str) -> dict:
    """Click a numbered control from app_find. The label must match what is there."""
    try:
        el, actual = _locate(window, ref, label)
        _bring_front(window)
        try:
            el.invoke()                                            # buttons, items
            how = "invoke"
        except Exception:                                          # noqa: BLE001
            el.click_input()
            how = "click"
        time.sleep(0.5)
        return {"clicked": actual, "ref": ref, "how": how, "window": window, "undo_ref": ""}
    except Exception as e:                                         # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


class AppType(BaseModel):
    window: str = Field(description="part of the window's title")
    ref: int = Field(description="the number of an Edit field from app_find")
    label: str = Field(description="the label shown next to that number, repeated exactly")
    text: str = Field(description="what to type")
    submit: bool = Field(default=False, description="press Enter afterwards")


@tool("app_type", AppType, mutating=True)
def app_type(window: str, ref: int, label: str, text: str, submit: bool = False) -> dict:
    """Type into a numbered field from app_find, optionally pressing Enter."""
    try:
        el, actual = _locate(window, ref, label)
        if "password" in actual.lower():
            raise PermissionError("that is a password field. Mudit types passwords himself.")
        _bring_front(window)
        el.set_focus()
        el.click_input()
        time.sleep(0.15)
        from pywinauto.keyboard import send_keys
        send_keys("^a{BACKSPACE}")
        # with_spaces + escaping: pywinauto's send_keys treats {}+^%~ specially.
        safe = "".join(f"{{{c}}}" if c in "{}+^%~()" else c for c in text)
        send_keys(safe, with_spaces=True, pause=0.01)
        time.sleep(0.2)
        value = ""
        try:
            value = el.get_value() if hasattr(el, "get_value") else el.window_text()
        except Exception:                                          # noqa: BLE001
            pass
        if submit:
            send_keys("{ENTER}")
            time.sleep(0.5)
        return {"typed_into": actual, "ref": ref, "value": value, "submitted": submit,
                "window": window, "undo_ref": ""}
    except Exception as e:                                         # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
