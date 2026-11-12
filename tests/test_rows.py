import pytest

from notion_tui import rows
from notion_tui.cache import Cache
from notion_tui.database import can_edit_rows, grid_columns, replace_row
from notion_tui.ntn import View


def test_editor_per_type():
    assert rows.editor("title") == "text"
    assert rows.editor("status") == "pick"
    assert rows.editor("people") == "multi"
    assert rows.editor("checkbox") == "check"
    assert rows.editor("formula") is None


def test_text_values():
    assert rows.parse_text("title", " Dune ") == ({"title": [{"text": {"content": "Dune"}}]}, "Dune")
    assert rows.parse_text("rich_text", "") == ({"rich_text": []}, "")
    assert rows.parse_text("url", "") == ({"url": None}, "")
    assert rows.parse_text("email", "a@b.c")[0] == {"email": "a@b.c"}


def test_long_text_is_split_for_notion_and_kept_whole_for_the_cell():
    value, shown = rows.parse_text("rich_text", "x" * 2500)
    assert [len(part["text"]["content"]) for part in value["rich_text"]] == [2000, 500]
    assert shown == "x" * 2500


def test_numbers():
    assert rows.parse_text("number", "5") == ({"number": 5}, "5")
    assert rows.parse_text("number", "2.5") == ({"number": 2.5}, "2.5")
    assert rows.parse_text("number", "") == ({"number": None}, "")
    for bad in ("five", "inf", "nan"):
        with pytest.raises(ValueError, match="isn't a number"):
            rows.parse_text("number", bad)


def test_dates():
    assert rows.parse_text("date", "2026-11-20") == ({"date": {"start": "2026-11-20"}}, "2026-11-20")
    value, shown = rows.parse_text("date", "2026-11-20 -> 2026-11-25")
    assert value == {"date": {"start": "2026-11-20", "end": "2026-11-25"}}
    assert shown == "2026-11-20 → 2026-11-25"
    assert rows.parse_text("date", "") == ({"date": None}, "")
    for bad in ("tomorrow", "2026-13-01", "2026-01-01 → 2026-01-02 → 2026-01-03"):
        with pytest.raises(ValueError):
            rows.parse_text("date", bad)


def test_edit_text_keeps_a_dates_time():
    prop = {"type": "date", "date": {"start": "2026-11-20T10:00:00.000+02:00", "end": None}}
    assert rows.edit_text(prop) == "2026-11-20T10:00:00.000+02:00"
    assert rows.parse_text("date", rows.edit_text(prop))[0]["date"]["start"] == "2026-11-20T10:00:00.000+02:00"


def test_copy_text_is_the_full_value():
    assert rows.copy_text({"type": "date", "date": {"start": "2026-11-20T10:00:00.000+02:00", "end": "2026-11-21"}}) == "2026-11-20T10:00:00.000+02:00 → 2026-11-21"
    assert rows.copy_text({"type": "created_time", "created_time": "2026-11-01T08:30:00.000Z"}) == "2026-11-01T08:30:00.000Z"
    long = "שלום עולם " * 10 + "end"
    assert rows.copy_text({"type": "rich_text", "rich_text": [{"plain_text": long}]}) == long
    assert rows.copy_text({"type": "rich_text", "rich_text": [{"plain_text": "one\ntwo"}]}) == "one\ntwo"
    assert rows.copy_text({"type": "date", "date": None}) == ""


def test_pick_values():
    assert rows.pick_value("select", ["Done"]) == ({"select": {"name": "Done"}}, "Done")
    assert rows.pick_value("status", []) == ({"status": None}, "")
    assert rows.pick_value("multi_select", ["a", "b"]) == ({"multi_select": [{"name": "a"}, {"name": "b"}]}, "a, b")
    assert rows.pick_value("people", ["Ann"], {"Ann": "u1"}) == ({"people": [{"id": "u1"}]}, "Ann")


def test_picked():
    assert rows.picked({"type": "select", "select": None}) == []
    assert rows.picked({"type": "status", "status": {"name": "Done"}}) == ["Done"]
    assert rows.picked({"type": "people", "people": [{"name": "Ann"}]}) == ["Ann"]


def test_toggled():
    assert rows.toggled({"type": "checkbox", "checkbox": False}) == ({"checkbox": True}, "✓")
    assert rows.toggled({"type": "checkbox", "checkbox": True}) == ({"checkbox": False}, "")


def test_new_options_refuse_commas():
    assert rows.check_option(" New ") == "New"
    with pytest.raises(ValueError):
        rows.check_option("a,b")
    assert rows.can_add_options("select") and not rows.can_add_options("status")


SCHEMA = [
    {"id": "title", "name": "Name", "type": "title", "options": []},
    {"id": "a", "name": "Rating", "type": "number", "options": []},
    {"id": "b", "name": "Who", "type": "created_by", "options": []},
    {"id": "c", "name": "When", "type": "created_time", "options": []},
]


def test_grid_columns():
    assert grid_columns(SCHEMA, None) == ["Name", "Rating"]  # empty columns stay: they can be filled in
    assert grid_columns(SCHEMA, ["Rating", "Who", "When", "Gone", "Name"]) == ["Rating", "When", "Name"]


def test_can_edit_rows():
    assert can_edit_rows(None)
    assert can_edit_rows(View("v", "", "list", None, None, None))
    assert can_edit_rows(View("v", "", "table", None, None, None))
    assert not can_edit_rows(View("v", "", "board", None, None, None))


def test_replace_row():
    data = [{"id": "a", "n": 1}, {"id": "b", "n": 2}]
    replace_row(data, {"id": "b", "n": 3})
    assert data[1]["n"] == 3


def test_edit_mode_survives_db_state_changes(tmp_path):
    cache = Cache(tmp_path / "c.db")
    assert not cache.db_edit_mode("ds")
    cache.put_db_edit_mode("ds", True)
    cache.put_db_state("ds", "v1", "a:b", None)
    assert cache.db_edit_mode("ds")
    assert cache.db_state("ds") == ("v1", "a:b", None)
    cache.put_db_edit_mode("ds", False)
    assert not cache.db_edit_mode("ds")
    assert cache.db_state("ds") == ("v1", "a:b", None)


def test_edit_mode_column_is_added_to_old_caches(tmp_path):
    import sqlite3

    path = tmp_path / "c.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE db_state (data_source_id TEXT PRIMARY KEY, view_id TEXT, query_text TEXT NOT NULL DEFAULT '', sorts_json TEXT)")
    old.execute("INSERT INTO db_state VALUES ('ds', 'v1', '', NULL)")
    old.commit()
    old.close()
    cache = Cache(path)
    assert not cache.db_edit_mode("ds")
    assert cache.db_state("ds") == ("v1", "", None)


def test_order_column():
    assert rows.order_column([{"name": "Name", "type": "title"}, {"name": "_ord", "type": "number"}]) == "_ord"
    assert rows.order_column([{"name": "_ord", "type": "rich_text"}]) is None
    assert rows.order_column([]) is None


def test_new_order():
    def ords(*values):
        return [{"properties": {"_ord": {"type": "number", "number": v}}} for v in values]

    assert rows.new_order(ords(1, 2), 0, True, "_ord") == 1.5
    assert rows.new_order(ords(1, 2), 1, False, "_ord") == 1.5
    assert rows.new_order(ords(1, 2), 1, True, "_ord") == 3  # last row
    assert rows.new_order(ords(1, 2), 0, False, "_ord") == 0  # first row
    assert rows.new_order(ords(1, None), 0, True, "_ord") == 2  # neighbour without _ord
    assert rows.new_order(ords(None, 2), 0, True, "_ord") is None  # cursor row without _ord
    assert rows.new_order(ords(5, 1), 0, True, "_ord") == 6  # not shown in _ord order
    assert rows.new_order(ords(3, 3), 0, True, "_ord") == 4  # a tie: halfway would be a tie too
    assert rows.new_order([{"properties": {}}], 0, True, "_ord") is None


def test_after_last():
    assert rows.after_last(5) == 13
    assert rows.after_last(None) == 8
    assert rows.after_last(-10) == -2


def test_insert_order():
    def ords(*values):
        return [{"properties": {"_ord": {"type": "number", "number": v}}} for v in values]

    assert rows.insert_order(ords(1, 2), 0, True, "_ord", False) == 1.5
    assert rows.insert_order(ords(1, 2), 1, True, "_ord", True) is rows.Order.NEEDS_MORE  # next row not loaded
    assert rows.insert_order(ords(1, 2), 1, True, "_ord", False) == 3
    assert rows.insert_order(ords(1, 2), 0, False, "_ord", True) == 0  # above: loaded neighbours enough
    assert rows.insert_order(ords(None, 2), 0, True, "_ord", True) is rows.Order.LAST  # below a row without _ord
    assert rows.insert_order(ords(None, 2), 0, False, "_ord", False) is None  # above one: left empty


def test_order_moves_swap_two_values():
    assert rows.order_moves([1, 2, 3.5], 1, 1) == {1: 3.5, 2: 2}
    assert rows.order_moves([1, 2, 3.5], 1, -1) == {0: 2, 1: 1}


def test_order_moves_stop_at_the_edges():
    assert rows.order_moves([1, 2], 0, -1) is None
    assert rows.order_moves([1, 2], 1, 1) is None


def test_order_moves_split_equal_values():
    assert rows.order_moves([1, 3, 3, 4], 1, 1) == {2: 3, 1: 3.5}  # the lower row goes on top, the other between 3 and 4
    assert rows.order_moves([3, 3], 0, 1) == {1: 3, 0: 4}


def test_order_moves_give_empty_values_numbers():
    assert rows.order_moves([1, 5, None], 1, 1) == {2: 5, 1: 6}  # empty sorts last
    assert rows.order_moves([1, 5, None, None], 3, -1) == {3: 6, 2: 7}


def test_order_moves_descending():
    assert rows.order_moves([5, 3, 1], 0, 1, descending=True) == {0: 3, 1: 5}
    assert rows.order_moves([5, 3, 3], 1, 1, descending=True) == {2: 3, 1: 2}


def test_order_descending():
    schema = [{"id": "ord", "name": "_ord", "type": "number", "options": []}]
    assert rows.order_descending(schema, "_ord", [{"property": "ord", "direction": "ascending"}]) is False
    assert rows.order_descending(schema, "_ord", [{"property": "ord", "direction": "descending"}]) is True
    assert rows.order_descending(schema, "_ord", [{"property": "x", "direction": "ascending"}, {"property": "ord"}]) is None
    assert rows.order_descending(schema, "_ord", None) is None


def test_move_row_swaps_the_rows_and_returns_what_to_save():
    listed = [{"id": i, "properties": {"_ord": {"type": "number", "number": n}}} for i, n in (("a", 1), ("b", 2), ("c", 3))]
    assert rows.move_row(listed, 2, -1, False, "_ord") == [("c", 2), ("b", 3)]
    assert [(r["id"], r["properties"]["_ord"]["number"]) for r in listed] == [("a", 1), ("c", 2), ("b", 3)]
    assert rows.move_row(listed, 0, -1, False, "_ord") is None
