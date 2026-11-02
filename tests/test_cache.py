from notion_tui.cache import Cache
from notion_tui.ntn import View


def test_views_schema_and_state(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    views = [
        View("v2", "All", "table", None, None, None, "2026-01-01"),
        View("v1", "Done", "list", {"property": "x", "checkbox": {"equals": True}},
             [{"property": "]kno", "direction": "ascending"}], [{"property_id": "title", "visible": True}]),
    ]
    assert cache.views("ds") == []
    cache.put_views("ds", views)
    assert cache.views("ds") == views  # same order, JSON round-trips
    cache.put_views("ds", views[:1])
    assert cache.views("ds") == views[:1]  # replaced, not merged

    assert cache.schema("ds") is None
    schema = [{"id": "title", "name": "Name", "type": "title", "options": []}]
    cache.put_schema("ds", schema)
    assert cache.schema("ds") == schema

    assert cache.db_state("ds") == (None, "", None)
    cache.put_db_state("ds", "v1", "area:work", [{"property": "]kno", "direction": "descending"}])
    assert cache.db_state("ds") == ("v1", "area:work", [{"property": "]kno", "direction": "descending"}])
    cache.put_db_state("ds", "v1", "", None)
    assert cache.db_state("ds") == ("v1", "", None)
