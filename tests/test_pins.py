"""Pinned pages: the real app on a fake Notion."""

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


class FakeNotion:
    """Search answers from `batches` (one list per call, the last repeated); no other call leaves the test."""

    def __init__(self, monkeypatch):
        self.batches = [[A, B, C]]
        self.delay = 0.0
        self.pages: dict[str, Page] = {}
        for name in ("search", "get_page", "get_markdown"):
            monkeypatch.setattr(ntn, name, getattr(self, name))
        monkeypatch.setattr(desktop, "prune_files", lambda: None)

    async def search(self, query="", cursor=None, page_size=50):
        await asyncio.sleep(self.delay)
        index = int(cursor or 0)
        more = str(index + 1) if index + 1 < len(self.batches) else None
        return self.batches[min(index, len(self.batches) - 1)], more

    async def get_page(self, page_id):
        return self.pages[page_id]

    async def get_markdown(self, page_id):
        return ""


@pytest.fixture
def notion(monkeypatch):
    return FakeNotion(monkeypatch)


def run_app(tmp_path, check, pinned=()):
    async def run():
        cache = Cache(tmp_path / "cache.db")
        cache.upsert_pages([A, B, C])
        for page in pinned:
            cache.set_pinned(page, True)
        app = NotionTUI(cache, Options())
        async with app.run_test() as pilot:
            await settle(app, pilot)
            await check(app, pilot)

    asyncio.run(run())


async def settle(app, pilot):
    """Wait for Notion calls, then for the preview they start (it waits for the cursor to rest)."""
    for _ in range(2):
        await pilot.pause(0.2)
        await app.workers.wait_for_complete()
    await pilot.pause()


def ids(app):
    return [p.id for p in app.shown]


def labels(app):
    return [item.label() for item in app.query(PageItem)]


def status(app):
    return str(app.query_one("#status").render())


def test_pinned_first():
    fresh_c = Page("c", "Oldest renamed", C.last_edited, "")
    # Recent list: every pinned page on top, even one not loaded; fresher copy wins.
    assert pinned_first([A, fresh_c], [A, fresh_c], [C, B], recent=True) == [fresh_c, B, A]
    # Search: only the pinned pages it found.
    assert pinned_first([A, C], [A, C], [C, B], recent=False) == [C, A]


def test_p_pins_to_top_with_icon_and_unpins(tmp_path, notion):
    async def check(app, pilot):
        assert ids(app) == ["a", "b", "c"]
        await pilot.press("G", "p")
        await settle(app, pilot)
        assert ids(app) == ["c", "a", "b"]
        assert labels(app)[0] == PIN + "Oldest"
        assert app.current.id == "c"  # the cursor follows the page
        await pilot.press("p")
        await settle(app, pilot)
        assert ids(app) == ["a", "b", "c"]
        assert not any(label.startswith(PIN) for label in labels(app))

    run_app(tmp_path, check)


def test_startup_begins_on_pinned_top_page(tmp_path, notion):
    async def check(app, pilot):
        assert ids(app) == ["c", "a", "b"]
        assert app.current.id == "c"

    run_app(tmp_path, check, pinned=[C])


def test_first_notion_list_keeps_the_cursor_on_top(tmp_path, notion):
    newer = Page("n", "Edited elsewhere", "2026-11-04T00:00:00.000Z", "", "workspace", "")
    notion.batches = [[newer, A, B, C]]
    notion.delay = 0.1  # comes in after the cached list is up

    async def check(app, pilot):
        assert ids(app) == ["n", "a", "b", "c"]
        assert app.current.id == "n"

    run_app(tmp_path, check)


def test_search_puts_a_pinned_page_from_a_later_batch_on_top(tmp_path, notion):
    async def check(app, pilot):
        notion.batches = [[A, B], [C]]  # the pinned page comes in the second batch only
        await pilot.press("slash", "e")
        await pilot.pause(0.6)  # past the search debounce
        await settle(app, pilot)
        assert ids(app) == ["c", "a", "b"]
        assert labels(app)[0] == PIN + "Oldest"
        assert "hidden" not in status(app)

    run_app(tmp_path, check, pinned=[C])


def test_status_does_not_count_a_pinned_page_as_hidden(tmp_path, notion):
    async def check(app, pilot):
        await app._show_pages([A])  # the pinned page is drawn without being loaded
        await pilot.pause()
        assert ids(app) == ["c", "a"]
        assert "hidden" not in status(app)

    run_app(tmp_path, check, pinned=[C])


def test_linked_page_goes_below_the_pins_and_pins_with_its_edit_time(tmp_path, notion):
    linked = Page("L", "Linked", "", "")  # reached by a link: no edit time
    notion.pages["L"] = Page("L", "Linked", "2026-10-01T00:00:00.000Z", "", "workspace", "")

    async def check(app, pilot):
        await app._go_to(linked)
        await pilot.pause()
        assert ids(app)[:2] == ["c", "L"]
        assert app.current.id == "L"
        await pilot.press("p")
        await settle(app, pilot)
        assert [p.id for p in app.cache.pinned_pages()] == ["c", "L"]
        assert app.cache.pinned_pages()[1].last_edited == "2026-10-01T00:00:00.000Z"

    run_app(tmp_path, check, pinned=[C])
