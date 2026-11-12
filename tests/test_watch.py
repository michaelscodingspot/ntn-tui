"""The once-a-minute check of the focused page: the real app on a fake Notion, the timer fired by hand."""

import asyncio

import pytest
from textual.screen import ModalScreen
from textual.widgets import Markdown

from notion_tui import ntn
from notion_tui.app import Breadcrumb, NotionTUI, PageItem, TitleLabel
from notion_tui.cache import Cache
from notion_tui.ntn import Page
from notion_tui.options import Options

OLD = "2026-01-01T10:00:00.000Z"  # long ago: an unchanged edit time means unchanged content
NEW = "2026-01-02T10:00:00.000Z"


class FakeNotion:
    def __init__(self, monkeypatch):
        self.page = Page("p", "Notes", OLD, "", "workspace", "")
        self.body = "first text"
        self.reads = 0
        for name in ("search", "get_page", "get_markdown"):
            monkeypatch.setattr(ntn, name, getattr(self, name))

    async def search(self, query="", cursor=None, page_size=50):
        return [self.page], None

    async def get_page(self, page_id):
        return self.page

    async def get_markdown(self, page_id):
        self.reads += 1
        return self.body


@pytest.fixture
def notion(monkeypatch):
    return FakeNotion(monkeypatch)


def drive(tmp_path, script):
    async def run():
        app = NotionTUI(Cache(tmp_path / "c.db"), Options())
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(app, pilot)
            await script(app, pilot)

    asyncio.run(run())


async def settle(app, pilot):
    await pilot.pause(0.2)
    await app.workers.wait_for_complete()
    await pilot.pause()


def preview(app) -> str:
    return app.query_one("#preview", Markdown).source


def test_a_page_edited_elsewhere_refreshes(tmp_path, notion):
    async def script(app, pilot):
        assert "first text" in preview(app)
        notion.page = Page("p", "Notes", NEW, "", "workspace", "")
        notion.body = "second text"
        app._watch_current()
        await settle(app, pilot)
        assert "second text" in preview(app)
        assert app.current.last_edited == NEW
        assert app.cache.content("p") == ("second text", NEW)

    drive(tmp_path, script)


def test_an_unchanged_page_is_not_read_again(tmp_path, notion):
    async def script(app, pilot):
        reads = notion.reads
        app._watch_current()
        await settle(app, pilot)
        assert notion.reads == reads

    drive(tmp_path, script)


def test_no_check_while_a_dialog_is_open(tmp_path, notion):
    async def script(app, pilot):
        await app.push_screen(ModalScreen())
        notion.page = Page("p", "Notes", NEW, "", "workspace", "")
        notion.body = "second text"
        app._watch_current()
        await settle(app, pilot)
        assert "first text" in preview(app)

    drive(tmp_path, script)


def test_a_page_renamed_elsewhere_shows_its_new_title(tmp_path, notion):
    async def script(app, pilot):
        notion.page = Page("p", "Journal", NEW, "", "workspace", "")
        app._watch_current()
        await settle(app, pilot)
        assert app.current.title == "Journal"
        assert app.query_one(Breadcrumb).parts[-1] == "Journal"
        assert str(app.query_one(PageItem).query_one(TitleLabel).content) == "Journal"
        assert app.cache.find_page("p").title == "Journal"

    drive(tmp_path, script)
