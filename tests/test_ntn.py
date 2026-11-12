import asyncio
import json

import pytest

from notion_tui import ntn


def fake_run(monkeypatch, replies):
    calls = []

    async def run(*args, stdin=None):
        calls.append(args)
        return replies[args[1]]

    monkeypatch.setattr(ntn, "_run", run)
    return calls


def test_database_of_follows_the_data_source_parent(monkeypatch):
    fake_run(monkeypatch, {
        "v1/data_sources/ds": json.dumps({"parent": {"type": "database_id", "database_id": "db"}}),
        "v1/databases/db": json.dumps({"data_sources": [{"id": "ds"}, {"id": "ds2"}]}),
    })
    database = asyncio.run(ntn.database_of("ds"))
    assert database == ntn.Database("db", ("ds", "ds2"))
    assert database.ids == {"db", "ds", "ds2"}


def test_database_of_reports_odd_output(monkeypatch):
    fake_run(monkeypatch, {"v1/data_sources/ds": json.dumps({"parent": {"type": "workspace"}})})
    with pytest.raises(ntn.NtnError):
        asyncio.run(ntn.database_of("ds"))


def test_trash_database_patches_in_trash(monkeypatch):
    calls = fake_run(monkeypatch, {"v1/databases/db": "{}"})
    asyncio.run(ntn.trash_database("db"))
    assert calls == [("api", "v1/databases/db", "-X", "PATCH", "-d", '{"in_trash": true}')]


def test_highest_number_asks_for_the_top_non_empty_value(monkeypatch):
    calls = fake_run(monkeypatch, {"v1/data_sources/ds/query": json.dumps(
        {"results": [{"properties": {"_ord": {"type": "number", "number": 7}}}], "has_more": True})})
    assert asyncio.run(ntn.highest_number("ds", "_ord")) == 7
    body = json.loads(calls[0][-1])
    assert body == {
        "page_size": 1,
        "filter": {"property": "_ord", "number": {"is_not_empty": True}},
        "sorts": [{"property": "_ord", "direction": "descending"}],
    }


def test_highest_number_when_every_row_is_empty(monkeypatch):
    fake_run(monkeypatch, {"v1/data_sources/ds/query": json.dumps({"results": [], "has_more": False})})
    assert asyncio.run(ntn.highest_number("ds", "_ord")) is None
