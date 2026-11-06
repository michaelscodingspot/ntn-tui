"""Edit mode end to end: the real app, driven by keys, on a fake Notion."""

import asyncio
import copy

import pytest
from textual.widgets import Input

from notion_tui import ntn
from notion_tui.app import NotionTUI, PageList, PreviewScroll
from notion_tui.cache import Cache
from notion_tui.grid import RowGrid
from notion_tui.links import Link
from notion_tui.modals import CellPickScreen, CellTextScreen, LinkPickScreen
from notion_tui.ntn import NtnError, Page, View
from notion_tui.options import Options

DB = Page("ds", "Books", "2026-11-01T00:00:00.000Z", "", "workspace", "", "data_source")
SCHEMA = [
    {"id": "title", "name": "Name", "type": "title", "options": []},
    {"id": "s", "name": "Status", "type": "select", "options": ["To read", "Done"]},
    {"id": "t", "name": "Tags", "type": "multi_select", "options": ["Tech", "Fiction"]},
    {"id": "r", "name": "Rating", "type": "number", "options": []},
    {"id": "o", "name": "Owned", "type": "checkbox", "options": []},
    {"id": "f", "name": "Score", "type": "formula", "options": []},
]
VIEWS = [View("v1", "Shelf", "list", None, None, None), View("v2", "Genres", "board", None, None, None)]


def row(id, name):
    return {"object": "page", "id": id, "url": "", "parent": {"type": "data_source_id", "data_source_id": "ds"}, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "Status": {"type": "select", "select": None},
        "Tags": {"type": "multi_select", "multi_select": []},
        "Rating": {"type": "number", "number": None},
        "Owned": {"type": "checkbox", "checkbox": False},
        "Score": {"type": "formula", "formula": {"type": "number", "number": 1}},
    }}


class FakeNotion:
    def __init__(self, monkeypatch):
        self.rows = [row("a", "Alpha"), row("b", "Beta")]
        self.trashed = {}
        self.calls = []
        self.fail = False
        for name in ("search", "get_schema", "list_view_ids", "query_rows", "update_row", "create_row", "trash", "restore"):
            monkeypatch.setattr(ntn, name, getattr(self, name))

    async def search(self, query="", cursor=None, page_size=50):
        return [DB], None

    async def get_schema(self, data_source_id):
        return copy.deepcopy(SCHEMA)

    async def list_view_ids(self, data_source_id):
        raise NtnError("views come from the cache here")

    async def query_rows(self, data_source_id, filter=None, sorts=None, start_cursor=None, page_size=100):
        return copy.deepcopy(self.rows), None

    def _check(self):
        if self.fail:
            raise NtnError("Notion said no")

    async def update_row(self, row_id, properties):
        self.calls.append((row_id, properties))
        self._check()
        target = next(r for r in self.rows if r["id"] == row_id)
        for name, value in properties.items():
            (kind, new), = value.items()
            if kind == "title":
                new = [{"plain_text": part["text"]["content"]} for part in new]
            target["properties"][name][kind] = new
        return copy.deepcopy(target)

    async def create_row(self, data_source_id, title):
        self._check()
        new = row(f"new{len(self.rows)}", title)
        self.rows.append(new)
        return copy.deepcopy(new)

    async def trash(self, page_id):
        self._check()
        index = next(i for i, r in enumerate(self.rows) if r["id"] == page_id)
        self.trashed[page_id] = (index, self.rows.pop(index))

    async def restore(self, page_id):
        self._check()
        index, restored = self.trashed.pop(page_id)
        self.rows.insert(index, restored)


@pytest.fixture
def notion(monkeypatch):
    return FakeNotion(monkeypatch)


def drive(tmp_path, script, edit=True):
    async def run():
        cache = Cache(tmp_path / "c.db")
        cache.put_schema("ds", SCHEMA)
        cache.put_views("ds", VIEWS)
        cache.upsert_pages([DB])
        cache.put_db_edit_mode("ds", edit)
        app = NotionTUI(cache, Options())
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            await script(app, pilot, app.query_one(RowGrid))

    asyncio.run(run())


async def settle(app, pilot):
    await pilot.pause(0.2)
    await app.workers.wait_for_complete()
    await pilot.pause()


async def go(app, pilot, *keys):
    await pilot.press(*keys)
    await settle(app, pilot)


def cell(grid, row_id, name):
    return str(grid.get_cell(row_id, name))


def test_last_mode_opens_without_taking_focus_from_the_list(tmp_path, notion):
    async def script(app, pilot, grid):
        assert grid.display and not app.query_one(PreviewScroll).display
        assert app.query_one(PageList).has_focus
        assert grid.row_count == 2 and grid.names == ["Name", "Status", "Tags", "Rating", "Owned", "Score"]
        assert str(app.query_one("#dbmode").render()) == "EDIT"

    drive(tmp_path, script)


@pytest.mark.parametrize("key", ["right", "l", "v"])
def test_keys_from_the_list_go_into_a_showing_grid(tmp_path, notion, key):
    async def script(app, pilot, grid):
        assert app.query_one(PageList).has_focus
        await go(app, pilot, key)
        assert grid.has_focus and grid.display and app.cache.db_edit_mode("ds")
        await go(app, pilot, "down")
        assert grid.current_row_id == "b"
        await go(app, pilot, "right", "l")
        assert grid.cursor_column == 2
        await go(app, pilot, "k", "left", "h")
        assert grid.current_row_id == "a" and grid.cursor_column == 0

    drive(tmp_path, script)


def test_l_still_opens_links_without_a_grid(tmp_path, notion):
    async def script(app, pilot, grid):
        app.links = [Link("https://example.com", "Example")]
        app.refresh_bindings()
        await go(app, pilot, "l")
        assert isinstance(app.screen, LinkPickScreen)

    drive(tmp_path, script, edit=False)


def test_v_switches_modes_and_remembers(tmp_path, notion):
    async def script(app, pilot, grid):
        assert not grid.display
        await go(app, pilot, "v")
        assert grid.display and grid.has_focus and app.cache.db_edit_mode("ds")
        await go(app, pilot, "v")
        assert not grid.display and not app.cache.db_edit_mode("ds")
        assert app.query_one(PreviewScroll).has_focus

    drive(tmp_path, script, edit=False)


def test_esc_leaves_edit_mode(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter")
        assert grid.has_focus and app.screen.has_class("full")
        await go(app, pilot, "escape")
        assert not grid.display and app.query_one(PreviewScroll).has_focus

    drive(tmp_path, script)


def test_v_keeps_the_focus_on_the_right_side(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "v")
        assert grid.display and grid.has_focus
        for _ in range(2):
            await go(app, pilot, "v")
            assert not grid.display and app.query_one(PreviewScroll).has_focus
            await go(app, pilot, "v")
            assert grid.display and grid.has_focus

    drive(tmp_path, script)


def test_esc_goes_back_to_the_list_in_split_layout(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "v")
        await go(app, pilot, "escape")
        assert not grid.display and app.query_one(PageList).has_focus

    drive(tmp_path, script)


def test_a_view_notion_refuses_heals_from_fresh_views(tmp_path, notion, monkeypatch):
    bad = {"property": "s"}
    good = View("v1", "Shelf", "list", None, None, None)
    asked = []

    async def query_rows(data_source_id, filter=None, sorts=None, start_cursor=None, page_size=100):
        asked.append(filter)
        if filter == bad:
            raise NtnError("body failed validation")
        return copy.deepcopy(notion.rows), None

    async def list_view_ids(data_source_id):
        return ["v1"]

    async def get_view(view_id):
        return good

    monkeypatch.setattr(ntn, "query_rows", query_rows)
    monkeypatch.setattr(ntn, "list_view_ids", list_view_ids)
    monkeypatch.setattr(ntn, "get_view", get_view)

    async def run():
        cache = Cache(tmp_path / "c.db")
        cache.put_schema("ds", SCHEMA)
        cache.put_views("ds", [View("v1", "Shelf", "list", bad, None, None)])
        cache.upsert_pages([DB])
        app = NotionTUI(cache, Options())
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            await settle(app, pilot)
            assert asked[0] == bad and asked[-1] is None
            assert app.db_rows and len(app.db_rows.rows) == 2

    asyncio.run(run())


def test_board_view_stays_in_view_mode(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "right_square_bracket")
        assert not grid.display and app.query_one(PreviewScroll).has_focus
        await go(app, pilot, "v")
        assert not grid.display and app.cache.db_edit_mode("ds")
        await go(app, pilot, "left_square_bracket")
        assert grid.display and grid.has_focus

    drive(tmp_path, script)


def test_edit_a_text_cell(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "j")
        assert grid.current_row_id == "b"
        await go(app, pilot, "enter")
        assert isinstance(app.screen, CellTextScreen)
        assert app.screen.query_one(Input).value == "Beta"
        await go(app, pilot, "2", "enter")
        assert cell(grid, "b", "Name") == "Beta2"
        assert notion.calls == [("b", {"Name": {"title": [{"text": {"content": "Beta2"}}]}})]
        assert "Beta2" in app.cache.content("ds")[0]  # View mode's table has it too

    drive(tmp_path, script)


def test_unchanged_text_is_not_saved(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "enter", "enter")
        assert notion.calls == []

    drive(tmp_path, script)


def test_bad_number_keeps_the_editor_open(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "l", "l", "l", "enter", "x", "enter")
        assert isinstance(app.screen, CellTextScreen) and notion.calls == []
        await go(app, pilot, "backspace", "4", "enter")
        assert cell(grid, "a", "Rating") == "4"
        assert notion.calls == [("a", {"Rating": {"number": 4}})]

    drive(tmp_path, script)


def test_pick_a_select_option(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "l", "enter")
        assert isinstance(app.screen, CellPickScreen)
        await go(app, pilot, "j", "enter")
        assert cell(grid, "a", "Status") == "Done"
        assert notion.calls == [("a", {"Status": {"select": {"name": "Done"}}})]

    drive(tmp_path, script)


def test_add_a_new_select_option(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "l", "enter", "a", "N", "e", "w", "enter")
        assert cell(grid, "a", "Status") == "New"
        assert "New" in app.cache.schema("ds")[1]["options"]

    drive(tmp_path, script)


def test_tick_multi_select_options(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "l", "l", "enter", "space", "j", "space", "enter")
        assert cell(grid, "a", "Tags") == "Tech, Fiction"
        assert notion.calls == [("a", {"Tags": {"multi_select": [{"name": "Tech"}, {"name": "Fiction"}]}})]

    drive(tmp_path, script)


def test_checkbox_flips_at_once(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "l", "l", "l", "l", "space")
        assert cell(grid, "a", "Owned") == "✓"
        assert notion.calls == [("a", {"Owned": {"checkbox": True}})]

    drive(tmp_path, script)


def test_read_only_cell_opens_nothing(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "l", "l", "l", "l", "l", "enter")
        assert not isinstance(app.screen, (CellTextScreen, CellPickScreen)) and notion.calls == []

    drive(tmp_path, script)


def test_failed_save_puts_the_old_value_back(tmp_path, notion):
    async def script(app, pilot, grid):
        notion.fail = True
        await go(app, pilot, "enter", "enter", "2", "enter")
        assert cell(grid, "a", "Name") == "Alpha"

    drive(tmp_path, script)


def test_new_row(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "n")
        assert isinstance(app.screen, CellTextScreen)
        await go(app, pilot, "G", "a", "m", "m", "a", "enter")
        assert grid.row_count == 3 and notion.rows[-1]["properties"]["Name"]["title"][0]["plain_text"] == "Gamma"
        assert grid.current_row_id == notion.rows[-1]["id"]

    drive(tmp_path, script)


def test_new_row_cancelled(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "n", "escape")
        assert grid.row_count == 2 and len(notion.rows) == 2
        assert grid.has_focus

    drive(tmp_path, script)


def test_failed_new_row_goes_away(tmp_path, notion):
    async def script(app, pilot, grid):
        notion.fail = True
        await go(app, pilot, "enter", "n", "Z", "enter")
        assert grid.row_count == 2

    drive(tmp_path, script)


def test_delete_and_undo(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "x")
        assert grid.row_count == 1 and [r["id"] for r in notion.rows] == ["b"]
        await go(app, pilot, "u")
        assert grid.row_count == 2 and [r["id"] for r in notion.rows] == ["a", "b"]
        assert grid.current_row_id == "a"
        await go(app, pilot, "u")  # nothing left to bring back
        assert grid.row_count == 2

    drive(tmp_path, script)


def test_failed_delete_puts_the_row_back(tmp_path, notion):
    async def script(app, pilot, grid):
        notion.fail = True
        await go(app, pilot, "enter", "x")
        assert grid.row_count == 2 and grid.current_row_id == "a"
        await go(app, pilot, "u")
        assert grid.row_count == 2  # nothing to undo: it never left

    drive(tmp_path, script)


def test_delete_every_row_then_add(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "x", "x")
        assert grid.row_count == 0
        await go(app, pilot, "n", "Z", "enter")
        assert grid.row_count == 1

    drive(tmp_path, script)


def test_filter_from_the_grid_returns_to_the_grid(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "v", "f")  # split view, where closing the line would otherwise focus the list
        await go(app, pilot, "escape")
        assert grid.has_focus

    drive(tmp_path, script, edit=False)
