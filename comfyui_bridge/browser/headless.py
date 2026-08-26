"""Temporary headless browser, Playwright-backed. Generic and reusable.

Sync API on purpose: callers run it in a worker thread (e.g. FastAPI's
``run_in_threadpool``) — Playwright's sync API refuses to run inside an asyncio
loop. Degrades honestly: ``is_available()`` reports whether Playwright + a
browser binary are installed, so callers can return a clear error instead of
crashing.
"""

from __future__ import annotations

from typing import Any


def is_available() -> tuple[bool, str]:
    """(usable, reason) — fast import check. Missing browser binary surfaces at
    launch time as a clear error rather than paying a launch on every probe."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return False, "playwright not installed (pip install playwright && playwright install chromium)"
    return True, "ok"


class TemporaryBrowser:
    """Context manager owning one throwaway page. Reusable for any page JS.

        with TemporaryBrowser() as b:
            b.goto("http://…")
            b.wait_for_function("() => window.ready")
            data = b.evaluate("() => document.title")
    """

    def __init__(self, timeout_ms: int = 30000, headless: bool = True) -> None:
        self._timeout = timeout_ms
        self._headless = headless
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self) -> "TemporaryBrowser":
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(self._timeout)
        return self

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def wait_for_function(self, js: str, timeout_ms: int | None = None) -> None:
        self._page.wait_for_function(js, timeout=timeout_ms or self._timeout)

    def evaluate(self, js: str, arg: Any = None) -> Any:
        return self._page.evaluate(js, arg)

    def __exit__(self, *exc: object) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()


def evaluate_on(url: str, script: str, wait_for: str | None = None,
                timeout_ms: int = 30000, headless: bool = True) -> Any:
    """One-shot: open ``url``, optionally wait for a JS condition, run ``script``."""
    with TemporaryBrowser(timeout_ms, headless) as b:
        b.goto(url)
        if wait_for:
            b.wait_for_function(wait_for)
        return b.evaluate(script)
