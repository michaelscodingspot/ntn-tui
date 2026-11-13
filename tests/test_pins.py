import asyncio

import pytest

from notion_tui import desktop, ntn
from notion_tui.app import NotionTUI, PageItem, PIN, pinned_first
from notion_tui.cache import Cache
from notion_tui.ntn import Page
from notion_tui.options import Options

A = Page("a", "Newest", "2026-11-03T00:00:00.000Z", "", "workspace", "")
B = Page("b", "Middle", "2026-11-02T00:00:00.000Z", "", "workspace", "")
C = Page("c", "Oldest", "2026-11-01T00:00:00.000Z", "", "workspace", "")


def test_cache_keeps_pins_in_pin_order_and_forget_drops_them(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.upsert_pages([A, B, C])
    cache.set_pinned("c", True)
    cache.set_pinned("a", True)
    cache.set_pinned("c", True)  # pinning twice keeps the first place
    assert [p.id for p in cache.pinned_pages()] == ["c", "a"]
    cache.set_pinned("c", False)
    assert [p.id for p in cache.pinned_pages()] == ["a"]
    cache.forget("a")
    cache.upsert_pages([A])
    assert cache.pinned_pages() == []


def test_pinned_first():
    fresh_c = Page("c", "Oldest renamed", C.last_edited, "")
    # Recent list: every pinned page on top, even one not loaded; fresher copy wins.
    assert pinned_first([A, fresh_c], [A, fresh_c], [C, B], recent=True) == [fresh_c, B, A]
    # Search: only the pinned pages it found.
    assert pinned_first([A, C], [A, C], [C, B], recent=False) == [C, A]


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(NotionTUI, "load_list", lambda *_: None)
    monkeypatch.setattr(desktop, "prune_files", lambda: None)


def run_app(tmp_path, check):
    async def run():
        cache = Cache(tmp_path / "cache.db")
        cache.upsert_pages([A, B, C])
        app = NotionTUI(cache, Options())
        async with app.run_test() as pilot:
            await pilot.pause()
            await check(app, pilot)

    asyncio.run(run())


def labels(app):
    return [item.label() for item in app.query(PageItem)]


def test_p_pins_to_top_with_icon_and_unpins(tmp_path, offline):
    async def check(app, pilot):
        assert [p.id for p in app.shown] == ["a", "b", "c"]
        await pilot.press("G", "p")
        await pilot.pause()
        assert [p.id for p in app.shown] == ["c", "a", "b"]
        assert labels(app)[0] == PIN + "Oldest"
        assert app.current.id == "c"  # the cursor follows the page
        await pilot.press("p")
        await pilot.pause()
        assert [p.id for p in app.shown] == ["a", "b", "c"]
        assert not any(label.startswith(PIN) for label in labels(app))

    run_app(tmp_path, check)


def test_startup_begins_on_pinned_top_page(tmp_path, offline):
    Cache(tmp_path / "cache.db").upsert_pages([C])
    Cache(tmp_path / "cache.db").set_pinned("c", True)

    async def check(app, pilot):
        assert [p.id for p in app.shown] == ["c", "a", "b"]
        assert app.current.id == "c"

    run_app(tmp_path, check)


def test_first_notion_list_keeps_the_cursor_on_top(tmp_path, monkeypatch):
    newer = Page("n", "Edited elsewhere", "2026-11-04T00:00:00.000Z", "", "workspace", "")

    async def search(query, cursor=None):
        await asyncio.sleep(0.05)  # comes in after the cached list is up
        return [newer, A, B, C], None

    monkeypatch.setattr(ntn, "search", search)
    monkeypatch.setattr(desktop, "prune_files", lambda: None)

    async def check(app, pilot):
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [p.id for p in app.shown] == ["n", "a", "b", "c"]
        assert app.current.id == "n"

    run_app(tmp_path, check)
