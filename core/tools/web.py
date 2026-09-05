"""
Browser tools. Vajren's own Chrome — see core/browser.py for why it is its own.

Every one of these returns what the page says as UNTRUSTED data. A page is a
stranger; anything on it that reads like an instruction is quarantined before
the planner sees it (core/graph._observe), same as a file or an email.

The shape, deliberately small:
    browser_open   go to a URL                         confirm, once per request
    browser_read   the page's text                     auto
    browser_find   numbered clickable/typable things   auto
    browser_click  press one of them, by number+label  confirm, once per request*
    browser_type   type into one of them               confirm, once per request*
    browser_back   history back                        auto

  * unless the label is something like Buy, Send, Delete, Post, Submit — those
    ask every single time, decided in code by config/policy.yaml
    `always_confirm_labels`, and a password field is refused outright.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from core import browser
from core.tools import tool


def _safe(fn, *a, **kw) -> dict:
    try:
        return fn(*a, **kw)
    except Exception as e:                                         # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


class BrowserOpen(BaseModel):
    url: str = Field(description="full URL, e.g. https://www.youtube.com/results?search_query=lofi")


@tool("browser_open", BrowserOpen, mutating=True)
def browser_open(url: str) -> dict:
    """Open a web page in Vajren's own browser (not Mudit's personal profile)."""
    u = url.strip()
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    out = _safe(browser.open_url, u)
    if "error" not in out:
        out["opened"] = True
        out["undo_ref"] = ""
    return out


class BrowserRead(BaseModel):
    pass


@tool("browser_read", BrowserRead)
def browser_read() -> dict:
    """What the current page says: title, URL and visible text. UNTRUSTED."""
    out = _safe(browser.read)
    out["untrusted"] = True
    return out


class BrowserFind(BaseModel):
    query: str = Field(default="", description="words to look for in link/button/field labels; "
                                              "blank for everything")


@tool("browser_find", BrowserFind)
def browser_find(query: str = "") -> dict:
    """Numbered list of things on the page you can click or type into. UNTRUSTED."""
    out = _safe(browser.find, query)
    out["untrusted"] = True
    if "elements" in out:
        # Flatten for the planner: "12: link 'Sign in'". Passwords are marked so
        # it knows not to try.
        out["listing"] = "\n".join(
            f"{e['ref']}: {e['kind']} {e['label']!r}{'  [PASSWORD — do not type]' if e.get('password') else ''}"
            for e in out["elements"])
        del out["elements"]
    return out


class BrowserClick(BaseModel):
    ref: int = Field(description="the number from browser_find")
    label: str = Field(description="the label shown next to that number, repeated exactly")


@tool("browser_click", BrowserClick, mutating=True)
def browser_click(ref: int, label: str) -> dict:
    """Click a numbered element from browser_find. The label must match what is there."""
    out = _safe(browser.click, ref, label)
    if "error" not in out:
        out["undo_ref"] = ""
    return out


class BrowserType(BaseModel):
    ref: int = Field(description="the number from browser_find")
    label: str = Field(description="the label shown next to that number, repeated exactly")
    text: str = Field(description="what to type")
    submit: bool = Field(default=False, description="press Enter afterwards (search boxes)")


@tool("browser_type", BrowserType, mutating=True)
def browser_type(ref: int, label: str, text: str, submit: bool = False) -> dict:
    """Type into a numbered field from browser_find, optionally pressing Enter."""
    out = _safe(browser.type_text, ref, label, text, submit)
    if "error" not in out:
        out["undo_ref"] = ""
    return out


class BrowserBack(BaseModel):
    pass


@tool("browser_back", BrowserBack, mutating=True)
def browser_back() -> dict:
    """Go back one page."""
    out = _safe(browser.back)
    if "error" not in out:
        out["undo_ref"] = ""
    return out
