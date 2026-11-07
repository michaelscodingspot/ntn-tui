"""A view's filter as read from Notion."""

import asyncio
import json

from notion_tui import ntn

STATUS = {"property": "s", "select": {"equals": "To read"}}


def view_with(monkeypatch, **fields):
    async def run(*args):
        return json.dumps({"id": "v", "name": "Shelf", "type": "list", **fields})

    monkeypatch.setattr(ntn, "_run", run)
    return asyncio.run(ntn.get_view("v"))


def test_a_quick_filter_with_no_value_is_left_out(monkeypatch):
    assert view_with(monkeypatch, filter=STATUS, quick_filters={"t": {}}).filter == STATUS


def test_only_empty_quick_filters_means_no_filter(monkeypatch):
    assert view_with(monkeypatch, quick_filters={"t": {}}).filter is None


def test_a_quick_filter_with_a_value_narrows_the_view(monkeypatch):
    view = view_with(monkeypatch, filter=STATUS, quick_filters={"t": {"select": {"equals": "Very"}}})
    assert view.filter == {"and": [STATUS, {"property": "t", "select": {"equals": "Very"}}]}
