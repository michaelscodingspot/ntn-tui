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
