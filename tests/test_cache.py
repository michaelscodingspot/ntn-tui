from notion_tui.cache import Cache
from notion_tui.ntn import Page, View


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


def test_forget_database_drops_its_rows_and_data_source_state(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.upsert_pages([
        Page("ds", "Books", "2026-01-01", "", "workspace", "", "data_source"),
        Page("row", "Piranesi", "2026-01-01", "", "database", "db"),
        Page("other", "Notes", "2026-01-01", "", "workspace", ""),
    ])
    cache.put_views("ds", [View("v", "All", "table", None, None, None)])
    cache.put_schema("ds", [])
    cache.put_db_state("ds", "v", "", None)

    cache.forget_database({"db", "ds"})

    assert [p.id for p in cache.recent_pages()] == ["other"]
    assert cache.views("ds") == [] and cache.schema("ds") is None
    assert cache.db_state("ds") == (None, "", None)


def test_wrap_rows_is_kept_per_database_next_to_edit_mode(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    assert not cache.db_wrap_rows("ds")
    cache.put_db_edit_mode("ds", True)
    cache.put_db_wrap_rows("ds", True)
    assert cache.db_wrap_rows("ds") and cache.db_edit_mode("ds") and not cache.db_wrap_rows("other")
    cache.put_db_wrap_rows("ds", False)
    assert not cache.db_wrap_rows("ds") and cache.db_edit_mode("ds")
