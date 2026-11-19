"""Shared test setup: no test opens a real browser or waits for its window."""

import pytest

from notion_tui import desktop


@pytest.fixture(autouse=True)
def opened(monkeypatch):
    """URLs the app asked the browser to open."""
    urls: list[str] = []

    async def no_window(title: str) -> bool:
        return False

    monkeypatch.setattr(desktop, "open_url", urls.append)
    monkeypatch.setattr(desktop, "raise_page_window", no_window)
    return urls


@pytest.fixture(autouse=True)
def no_content_index(monkeypatch):
    """The background content indexer stays off, so fake Notions see only the calls a test makes.
    tests/test_search.py turns it back on for its own app test."""
    from notion_tui.app import NotionTUI

    monkeypatch.setattr(NotionTUI, "index_content", lambda self: None)
