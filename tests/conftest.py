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


def pytest_configure(config):
    config.addinivalue_line("markers", "indexer: keep the background content indexer on for this test")


@pytest.fixture(autouse=True)
def no_content_index(request, monkeypatch):
    """The background content indexer stays off, so fake Notions see only the calls a test makes.
    A test marked `indexer` keeps it on."""
    if request.node.get_closest_marker("indexer"):
        return
    from notion_tui.app import NotionTUI

    monkeypatch.setattr(NotionTUI, "index_content", lambda self: None)
