from notion_tui.app import rows_markdown, sort_label, view_columns
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
