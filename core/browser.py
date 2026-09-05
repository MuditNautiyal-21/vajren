"""
Vajren's own browser. One Chrome, one profile, one thread.

WHY A SEPARATE PROFILE, and this is not a shortcut: Chrome 136 (spring 2025)
refuses --remote-debugging-port on the default user-data directory, on purpose,
so that a local process cannot puppet a person's logged-in browser. Mudit's
Chrome is 152. So Vajren cannot drive the PCYT profile, and it should not want
to — a page that gets instructions past the quarantine can then only act as
Vajren's browser, never as Mudit. He logs Vajren's browser into what it needs,
once, deliberately. That is the boundary.

WHY ONE THREAD: Playwright's sync API is bound to the thread that created it.
Tools are called from whatever worker asyncio hands them. Every browser call
goes through a single-worker executor so they all land on the same thread.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "browser" / "profile"
DOWNLOADS = ROOT / "browser" / "downloads"

_pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vajren-browser")
_lock = threading.Lock()
_state: dict[str, Any] = {"pw": None, "ctx": None, "page": None, "refs": 0}

# Interactive things a person could click or type into, found and numbered in
# the page itself. The number is what the planner clicks; the label is what it
# must repeat back, so the gate can see what is about to be pressed.
_SNAPSHOT_JS = r"""
(max) => {
  const sel = 'a[href], button, input, textarea, select, summary, ' +
    '[role=button], [role=link], [role=textbox], [role=tab], [role=menuitem], ' +
    '[role=checkbox], [role=option], [role=combobox], [contenteditable=true]';
  const out = []; let n = 0;
  const vis = el => { const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && st.visibility !== 'hidden' &&
      st.display !== 'none' && r.bottom > 0 && r.top < innerHeight * 3; };
  const name = el => (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
    el.getAttribute('title') || el.getAttribute('alt') || el.innerText || el.value ||
    el.getAttribute('name') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  document.querySelectorAll('[data-vj]').forEach(e => e.removeAttribute('data-vj'));
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && ['hidden', 'submit', 'button', 'image'].includes(type) && !name(el)) continue;
    const label = name(el);
    if (!label && tag !== 'input' && tag !== 'textarea') continue;
    n += 1; el.setAttribute('data-vj', String(n));
    out.push({ ref: n, kind: el.getAttribute('role') || (tag === 'a' ? 'link' :
      tag === 'input' ? (type || 'text') : tag), label, password: type === 'password' });
    if (out.length >= max) break;
  }
  return out;
}
"""


def _run(fn: Callable[[], Any], timeout: float = 60) -> Any:
    return _pool.submit(fn).result(timeout=timeout)


def _ensure() -> Any:
    """The page, starting Chrome on first use. Must run on the browser thread."""
    if _state["page"] is not None:
        try:
            _state["page"].title()
            return _state["page"]
        except Exception:                                          # noqa: BLE001
            _state.update(pw=None, ctx=None, page=None)
    from playwright.sync_api import sync_playwright
    PROFILE.mkdir(parents=True, exist_ok=True)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=False,
        viewport=None, accept_downloads=True, downloads_path=str(DOWNLOADS),
        args=["--start-maximized", "--disable-blink-features=AutomationControlled",
              "--no-first-run", "--no-default-browser-check"],
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    _state.update(pw=pw, ctx=ctx, page=page)
    return page


def _snapshot(page, max_items: int = 60) -> list[dict]:
    items = page.evaluate(_SNAPSHOT_JS, max_items)
    _state["refs"] = len(items)
    return items


def _settle(page, ms: int = 800) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:                                              # noqa: BLE001
        pass
    page.wait_for_timeout(ms)


def _page_text(page, limit: int = 4000) -> str:
    try:
        t = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:                                              # noqa: BLE001
        t = ""
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t[:limit]


# ------------------------------------------------------------ operations --
def open_url(url: str) -> dict:
    def go():
        page = _ensure()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _settle(page)
        return {"url": page.url, "title": page.title()}
    return _run(go, 45)


def read() -> dict:
    def go():
        page = _ensure()
        return {"url": page.url, "title": page.title(), "content": _page_text(page)}
    return _run(go)


def find(query: str = "", max_items: int = 40) -> dict:
    def go():
        page = _ensure()
        items = _snapshot(page, 120)
        q = (query or "").strip().lower()
        if q:
            words = [w for w in re.split(r"\W+", q) if w]
            scored = []
            for it in items:
                hay = (it["label"] + " " + it["kind"]).lower()
                score = sum(1 for w in words if w in hay)
                if score:
                    scored.append((-score, it["ref"], it))
            items = [it for _, _, it in sorted(scored)]
        return {"url": page.url, "title": page.title(),
                "elements": items[:max_items], "count": len(items)}
    return _run(go)


def _locate(page, ref: int, label: str):
    loc = page.locator(f'[data-vj="{int(ref)}"]')
    if loc.count() == 0:
        _snapshot(page, 120)
        loc = page.locator(f'[data-vj="{int(ref)}"]')
        if loc.count() == 0:
            raise LookupError(f"nothing numbered {ref} on this page any more — call browser_find again")
    actual = page.evaluate(
        """(n) => { const el = document.querySelector('[data-vj="' + n + '"]');
                   return el ? ((el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                     el.getAttribute('title') || el.innerText || el.value || '')
                     .replace(/\\s+/g, ' ').trim().slice(0, 80)) : null; }""", int(ref))
    want = (label or "").strip().lower()
    have = (actual or "").strip().lower()
    # The label the planner repeated must be the label on the thing. This is
    # what stops "click 12 (Cancel)" from pressing a button that says Buy.
    if want and have and want not in have and have not in want:
        raise LookupError(f"element {ref} is labelled {actual!r}, not {label!r}. "
                          f"Call browser_find again and use the label you see.")
    return loc.first, actual


def click(ref: int, label: str) -> dict:
    def go():
        page = _ensure()
        before = page.url
        loc, actual = _locate(page, ref, label)
        loc.scroll_into_view_if_needed(timeout=5000)
        loc.click(timeout=8000)
        _settle(page)
        return {"clicked": actual, "ref": ref, "url_before": before, "url": page.url,
                "title": page.title(), "navigated": page.url != before}
    return _run(go)


def type_text(ref: int, label: str, text: str, submit: bool) -> dict:
    def go():
        page = _ensure()
        loc, actual = _locate(page, ref, label)
        if page.evaluate("(n) => { const e = document.querySelector('[data-vj=\"'+n+'\"]');"
                         " return !!e && (e.getAttribute('type')||'').toLowerCase() === 'password'; }",
                         int(ref)):
            # Vajren never handles a password. Not "asks first" — never. A
            # password typed by an assistant is a password that has been
            # through a language model, a log, and a transcript.
            raise PermissionError("that is a password field. Mudit types passwords himself.")
        loc.click(timeout=8000)
        loc.fill("", timeout=5000)
        loc.type(text, delay=20, timeout=15000)
        value = loc.input_value(timeout=3000) if loc.evaluate(
            "e => e.tagName === 'INPUT' || e.tagName === 'TEXTAREA'") else loc.inner_text()
        before = page.url
        if submit:
            loc.press("Enter")
            _settle(page)
        return {"typed_into": actual, "ref": ref, "value": value, "submitted": submit,
                "url": page.url, "title": page.title(), "navigated": page.url != before}
    return _run(go)


def back() -> dict:
    def go():
        page = _ensure()
        before = page.url
        page.go_back(wait_until="domcontentloaded", timeout=15000)
        _settle(page, 400)
        return {"url": page.url, "title": page.title(), "navigated": page.url != before}
    return _run(go)


def close() -> None:
    def go():
        for k in ("ctx", "pw"):
            try:
                if _state[k]:
                    (_state[k].close() if k == "ctx" else _state[k].stop())
            except Exception:                                      # noqa: BLE001
                pass
        _state.update(pw=None, ctx=None, page=None)
    try:
        _run(go, 15)
    except Exception:                                              # noqa: BLE001
        pass
