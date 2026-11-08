"""Database View and Edit modes end to end: the real app, driven by keys, on a fake Notion."""

import asyncio
import copy

import pytest
from textual.widgets import Input, Markdown

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
        self.queries = 0
        self.cursor = None  # set: the first batch of rows says there are more
        self.gate = None  # set: row queries wait for this event
        self.write_gate = None  # set: row updates wait for this event
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
        self.queries += 1
        if self.gate:
            await self.gate.wait()
        return copy.deepcopy(self.rows), None if start_cursor else self.cursor

    def _check(self):
        if self.fail:
            raise NtnError("Notion said no")

    async def update_row(self, row_id, properties):
        self.calls.append((row_id, properties))
        if self.write_gate:
            await self.write_gate.wait()
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


@pytest.mark.parametrize("key", ["right", "l", "v", "e"])
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


def test_e_goes_into_edit_mode_and_the_grid(tmp_path, notion):
    async def script(app, pilot, grid):
        assert not grid.display and app.query_one(PageList).has_focus
        await go(app, pilot, "e")
        assert grid.display and grid.has_focus and app.cache.db_edit_mode("ds")
        await go(app, pilot, "escape", "e")  # esc left Edit mode; e goes back in
        assert grid.display and grid.has_focus and app.cache.db_edit_mode("ds")

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
        await go(app, pilot, "e")
        assert not grid.display and app.query_one(PreviewScroll).has_focus
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


@pytest.mark.parametrize("key", ["y", "ctrl+c"])
def test_copy_a_cell(tmp_path, notion, key):
    async def script(app, pilot, grid):
        copied = []
        app.copy_to_clipboard = copied.append
        await go(app, pilot, "enter", "j", key)
        assert copied == ["Beta"]
        assert app.is_running  # ctrl+c copies here, it doesn't ask to quit
        await go(app, pilot, "l", key)  # Status is empty: nothing copied
        assert copied == ["Beta"]

    drive(tmp_path, script)


def test_copy_gives_the_full_text_not_the_cut_cell(tmp_path, notion):
    long = "שלום עולם " * 30 + "the end"
    notion.rows[0] = row("a", long)  # longer than the grid is wide, right-to-left words

    async def script(app, pilot, grid):
        copied = []
        app.copy_to_clipboard = copied.append
        await go(app, pilot, "enter", "y")
        assert "…" in cell(grid, "a", "Name")  # the cell is cut short
        assert copied == [long.strip()]

    drive(tmp_path, script)


def test_copy_waits_for_a_save(tmp_path, notion):
    async def script(app, pilot, grid):
        copied = []
        app.copy_to_clipboard = copied.append
        notion.write_gate = asyncio.Event()
        await go(app, pilot, "enter", "enter", "2")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")  # the row shows … : the old value isn't copied
        await pilot.pause()
        assert copied == []
        notion.write_gate.set()
        await settle(app, pilot)
        await go(app, pilot, "y")
        assert copied == ["Alpha2"]

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


def preview(app):
    return app.query_one("#preview", Markdown).source


def info(app):
    return str(app.query_one("#dbinfo").render())


def test_t_switches_the_layout_without_asking_notion_or_saving(tmp_path, notion):
    async def script(app, pilot, grid):
        assert preview(app).startswith("- **Alpha**")  # a list view opens as a list
        assert "layout" not in info(app)
        queries = notion.queries
        await go(app, pilot, "t")
        assert preview(app).startswith("| Name |")
        assert "layout: table (custom)" in info(app)
        assert notion.queries == queries
        assert app.cache.db_state("ds") == (None, "", None)
        assert app.cache.content("ds")[0].startswith("- **Alpha**")
        await go(app, pilot, "F")  # a reload draws the picked layout, and doesn't cache it either
        assert preview(app).startswith("| Name |")
        assert app.cache.content("ds")[0].startswith("- **Alpha**")
        await go(app, pilot, "t")
        assert preview(app).startswith("- **Alpha**")
        assert "layout" not in info(app)

    drive(tmp_path, script, edit=False)


def test_a_picked_layout_stays_with_its_view_until_the_app_quits(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "t")
        await go(app, pilot, "right_square_bracket")
        await go(app, pilot, "left_square_bracket")
        assert preview(app).startswith("| Name |")
        await go(app, pilot, "r")
        assert preview(app).startswith("| Name |")

    drive(tmp_path, script, edit=False)


def test_t_does_nothing_in_edit_mode(tmp_path, notion):
    async def script(app, pilot, grid):
        assert grid.display
        before = preview(app)
        await go(app, pilot, "v", "t")
        assert grid.has_focus and preview(app) == before
        assert not app._layouts

    drive(tmp_path, script)


def test_t_does_nothing_on_a_board_view(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "right_square_bracket")
        before = preview(app)
        await go(app, pilot, "t")
        assert preview(app) == before and not app._layouts

    drive(tmp_path, script, edit=False)


def test_more_rows_stay_in_the_picked_layout(tmp_path, notion):
    notion.cursor = "next"

    async def script(app, pilot, grid):
        await go(app, pilot, "t")
        await go(app, pilot, "m")
        assert preview(app).startswith("| Name |")
        assert preview(app).count("Alpha") == 2

    drive(tmp_path, script, edit=False)


def test_t_during_a_reload_leaves_the_old_rows_alone_and_the_reload_draws_it(tmp_path, notion):
    async def script(app, pilot, grid):
        notion.gate = asyncio.Event()
        await pilot.press("F")
        await pilot.pause(0.1)
        await pilot.press("t")
        await pilot.pause(0.1)
        assert preview(app).startswith("- **Alpha**")
        assert "layout: table (custom)" in info(app) and "loading…" in info(app)
        notion.gate.set()
        await settle(app, pilot)
        assert preview(app).startswith("| Name |")

    drive(tmp_path, script, edit=False)


def test_a_long_title_fills_the_grid_width(tmp_path, notion):
    async def script(app, pilot, grid):
        await go(app, pilot, "enter", "enter")
        app.screen.query_one(Input).value = "word " * 40
        await go(app, pilot, "enter")
        shown = cell(grid, "a", "Name")
        room = grid.scrollable_content_region.width
        assert shown.endswith("…") and len(shown) > 40
        assert room - 4 <= grid.virtual_size.width <= room  # wide enough to fill it, never past it
        await pilot.resize_terminal(160, 40)
        await settle(app, pilot)
        wider = cell(grid, "a", "Name")
        assert len(wider) > len(shown)  # a wider grid shows more of it
        await pilot.resize_terminal(100, 40)
        await settle(app, pilot)
        assert len(cell(grid, "a", "Name")) < len(shown)  # and a narrower one less, never past its edge
        assert grid.virtual_size.width <= grid.scrollable_content_region.width

    drive(tmp_path, script)


def test_more_rows_than_fit_still_fill_the_width_without_scrolling_sideways(tmp_path, notion):
    notion.rows = [row(f"r{i}", f"{i} " + "word " * 30) for i in range(60)] + [row("he", "שלום " * 30)]

    async def script(app, pilot, grid):
        await go(app, pilot, "enter")
        room = grid.scrollable_content_region.width
        assert grid.virtual_size.height > grid.size.height  # taller than the pane
        assert room - 4 <= grid.virtual_size.width <= room
        hebrew = cell(grid, "he", "Name")
        assert hebrew.startswith("…") and "שלום" in hebrew  # cut at its logical end, shown on the left

    drive(tmp_path, script)
