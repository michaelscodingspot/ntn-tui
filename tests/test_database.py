from notion_tui import database
from notion_tui.database import (
    rows_key,
    can_switch_layout,
    next_layout,
    rows_markdown,
    sort_label,
    trash_question,
    view_columns,
)
from notion_tui.ntn import View

SCHEMA = [
    {"id": "title", "name": "Name", "type": "title", "options": []},
    {"id": "]kno", "name": "Priority ", "type": "select", "options": []},
    {"id": "who", "name": "Owner", "type": "created_by", "options": []},
]


def _row(name, priority):
    return {"properties": {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "Priority ": {"type": "select", "select": {"name": priority} if priority else None},
        "Owner": {"type": "created_by", "created_by": {}},
    }}


def test_view_columns_adds_title_and_keeps_order():
    view = View("v", "Board", "board", None, None, [
        {"property_id": "]kno", "visible": True}, {"property_id": "who", "visible": False}, {"property_id": "gone", "visible": True},
    ])
    assert view_columns(view, SCHEMA) == ["Name", "Priority "]
    assert view_columns(None, SCHEMA) is None


def test_sort_label():
    sorts = [{"property": "]kno", "direction": "descending"}, {"timestamp": "created_time", "direction": "ascending"}]
    assert sort_label(sorts, SCHEMA) == "Priority ↓, created time ↑"
    assert sort_label(None, SCHEMA) == ""


def test_rows_markdown_with_view_columns_keeps_empty_drops_unrendered():
    md = rows_markdown([_row("a", None)], True, ["Name", "Priority ", "Owner"])
    lines = md.splitlines()
    assert lines[0] == "| Name | Priority |"
    assert lines[2] == "| a |  |"
    assert "Press m for more" in md


def test_rows_markdown_list_layout_puts_title_first_and_skips_empty_values():
    md = rows_markdown([_row("a", "High"), _row("", None)], True, ["Priority ", "Name"], layout="list")
    assert md.splitlines()[:2] == ["- **a** · High", "- **Untitled**"]
    assert "Press m for more" in md


def test_layouts_switch_only_on_list_and_table_views():
    assert can_switch_layout(View("v", "All", "table", None, None, None))
    assert can_switch_layout(View("v", "All", "list", None, None, None))
    assert not can_switch_layout(View("v", "Board", "board", None, None, None))
    assert not can_switch_layout(None)
    assert next_layout("table") == "list" and next_layout("list") == "table"


def test_trash_question_warns_about_other_data_sources():
    assert trash_question("Books", 1) == "Move database “Books” and all its rows to the Notion trash?"
    assert "all 3 of its data sources" in trash_question("Books", 3)


def test_column_widths_give_a_long_column_the_room_left():
    assert database.column_widths([4, 200], 100) == [4, 96]
    assert database.column_widths([10, 20], 100) == [10, 20]
    assert database.column_widths([60, 60], 100) == [50, 50]
    assert database.column_widths([30, 5, 30], 41) == [18, 5, 18]
    assert database.column_widths([50, 50], 4) == [8, 8]


def test_rows_key_sees_values_order_and_edit_time():
    a = {"id": "a", "last_edited_time": "t1", "properties": {"x": 1}}
    b = {"id": "b", "last_edited_time": "t1", "properties": {"x": 2}}
    assert rows_key([a, b]) == rows_key([dict(a), dict(b)])
    assert rows_key([a, b]) != rows_key([b, a])
    assert rows_key([a]) != rows_key([a | {"properties": {"x": 9}}])  # same minute, new value


def test_insert_next_to():
    rows = [{"id": "a"}, {"id": "b"}]
    database.insert_next_to(rows, {"id": "x"}, "a", True)
    database.insert_next_to(rows, {"id": "y"}, "a", False)
    database.insert_next_to(rows, {"id": "z"}, "gone", True)
    assert [r["id"] for r in rows] == ["y", "a", "x", "b", "z"]


def test_wrap_lines_keeps_breaks_wraps_words_and_caps():
    from notion_tui.database import natural_width, wrap_lines
    assert wrap_lines("Goals:\n- ship it", 20) == ["Goals:", "- ship it"]
    assert wrap_lines("one two three four", 9) == ["one two", "three", "four"]
    assert wrap_lines("abcdefghij", 4) == ["abcd", "efgh", "ij"]
    assert wrap_lines("", 5) == [""]
    assert wrap_lines("\n".join("abcdef"), 4, most=3) == ["a", "b", "c…"]
    assert wrap_lines("\n".join(["abcd"] * 3), 4, most=2) == ["abcd", "abc…"]
    assert natural_width("ab\nabcd", wrap=True) == 4
    assert natural_width("ab\nabcd", wrap=False) == 7
