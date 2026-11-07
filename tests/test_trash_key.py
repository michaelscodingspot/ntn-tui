import asyncio

import pytest

from notion_tui import desktop, ntn
from notion_tui.app import NotionTUI
from notion_tui.cache import Cache
from notion_tui.modals import ConfirmScreen
from notion_tui.ntn import Page
from notion_tui.options import Options


@pytest.fixture
def trashed(monkeypatch):
    """Keeps the app off Notion and the real file cache; lists the ids sent to trash."""
    sent = []

    async def trash(page_id):
        sent.append(page_id)

    monkeypatch.setattr(ntn, "trash", trash)
    monkeypatch.setattr(NotionTUI, "load_list", lambda *_: None)
    monkeypatch.setattr(desktop, "prune_files", lambda: None)
    return sent


def with_page_highlighted(tmp_path, check):
    async def run():
        app = NotionTUI(Cache(tmp_path / "cache.db"), Options())
        async with app.run_test() as pilot:
            app.current = Page("p1", "Title", "2026-11-01T00:00:00.000Z", "")
            await pilot.pause()
            await check(app, pilot)

    asyncio.run(run())


def test_x_asks_first_and_y_trashes_the_page(tmp_path, trashed):
    async def check(app, pilot):
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert trashed == []
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()

    with_page_highlighted(tmp_path, check)
    assert trashed == ["p1"]


def test_n_cancels_without_trashing(tmp_path, trashed):
    async def check(app, pilot):
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)

    with_page_highlighted(tmp_path, check)
    assert trashed == []


def test_d_no_longer_trashes(tmp_path, trashed):
    async def check(app, pilot):
        await pilot.press("d")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)

    with_page_highlighted(tmp_path, check)
    assert trashed == []
