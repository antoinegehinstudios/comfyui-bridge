"""Reusable temporary headless browser.

Generic capability: launch a short-lived headless browser, run JavaScript on a
page, get the result, tear down. Knows nothing about ComfyUI — any feature that
needs a throwaway browser (run page JS, scrape, convert a client-side format)
builds on this. The workflow extractor is its first consumer.
"""

from .headless import TemporaryBrowser, evaluate_on, is_available

__all__ = ["TemporaryBrowser", "evaluate_on", "is_available"]
