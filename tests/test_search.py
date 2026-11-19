import asyncio
import time

import pytest

from notion_tui import ntn
from notion_tui.app import NotionTUI, PageItem
from notion_tui.cache import Cache
from notion_tui.ntn import Page
from notion_tui.options import Options
from notion_tui.search import SNIPPET_AFTER, content_matches, plain_text, snippet, title_matches, with_content_matches


def page(pid: str, title: str, edited: str = "2026-01-01") -> Page:
    return Page(pid, title, edited, "", "workspace", "")


def test_plain_text_drops_tags_links_and_marks_keeps_properties():
    md = "---\nStatus: Done\n---\n# Plan\n\n- [ ] call **Dana** <br/> see [the doc](https://x.y/z)\n<empty-block/>\n"
    assert plain_text(md) == "Status: Done Plan call Dana see the doc"


def test_snippet_marks_the_match_and_starts_on_a_word():
    md = "Some long opening words before the budget numbers for next year"
    text, start, end = snippet(md, "BUDGET")
    assert text[start:end] == "budget"
    assert text.startswith("…") and not text.startswith("… ")
    assert text.endswith("next year")


def test_snippet_keeps_a_row_of_text_after_the_match_not_the_whole_page():
    text, start, end = snippet("budget " + "word " * 10000, "budget")
    assert (start, end) == (0, 6) and len(text) == 6 + SNIPPET_AFTER


def test_snippet_near_the_start_has_no_ellipsis():
    assert snippet("budget first", "budget") == ("budget first", 0, 6)


def test_snippet_marks_the_match_where_lowercasing_changes_length():
    text, start, end = snippet("İstanbul trip budget", "budget")
    assert text[start:end] == "budget"


def test_snippet_none_when_only_a_link_target_matches():
    assert snippet("see [the doc](https://budget.example)", "budget") is None
    assert snippet("anything", "  ") is None


def test_title_matches_come_first_and_show_no_snippet():
    a, b, c = page("a", "Budget 2027"), page("b", "Notes"), page("c", "Trips")
    titles = title_matches([a, b, c], "budget")
    assert titles == [a]
    found = content_matches([(b, "the budget"), (a, "budget"), (c, "budget too")], "budget")
    pages, snippets = with_content_matches(titles, found)
    assert pages == [a, b, c]
    assert set(snippets) == {"b", "c"}


def test_content_matches_skip_link_only_matches_without_counting_them():
    rows = [(page(str(i), "x"), "[doc](https://budget.example)") for i in range(5)]
    rows.append((page("real", "x"), "the budget"))
    assert [p.id for p, _ in content_matches(rows, "budget", limit=1)] == ["real"]


def test_cache_content_search_and_unindexed(tmp_path):
    cache = Cache(tmp_path / "c.db")
    old, new = page("old", "Old", "2026-01-01"), page("new", "New", "2026-02-01")
    db = Page("ds", "Books", "2026-01-01", "", "workspace", "", "data_source")
    cache.upsert_pages([old, new, db])
    assert {p.id for p in cache.unindexed_pages()} == {"old", "new"}  # databases have no Markdown to cache

    cache.put_content(old, "the Budget is here")
    cache.put_content(new, "budget again")
    assert [(p.id, md) for p, md in cache.content_containing("BUDGET")] == [
        ("new", "budget again"), ("old", "the Budget is here")]  # newest edit first
    assert cache.unindexed_pages() == []

    cache.upsert_pages([page("old", "Old", "2026-03-01")])  # edited since: fetched again
    assert [p.id for p in cache.unindexed_pages()] == ["old"]


def test_content_cached_within_its_edit_minute_is_fetched_again(tmp_path, monkeypatch):
    cache = Cache(tmp_path / "c.db")
    p = page("p", "P", "2026-11-19T10:00:00.000Z")  # Notion's minute-rounded edit time
    cache.upsert_pages([p])
    edit = 1795082400.0  # 2026-11-19 10:00:00 UTC

    monkeypatch.setattr("notion_tui.cache.time.time", lambda: edit + 20)
    cache.put_content(p, "first text")  # a second edit at 10:00:40 would keep the same edit time
    assert [q.id for q in cache.unindexed_pages()] == ["p"]

    monkeypatch.setattr("notion_tui.cache.time.time", lambda: edit + 75)
    cache.put_content(p, "final text")  # fetched after the minute: settled until the next edit
    assert cache.unindexed_pages() == []


def test_cache_content_search_folds_non_ascii_case(tmp_path):
    cache = Cache(tmp_path / "c.db")
    p = page("p", "P")
    cache.upsert_pages([p])
    cache.put_content(p, "Notes from the ÉCOLE meeting")
    assert [q.id for q, _ in cache.content_containing("école")] == ["p"]


def test_forget_unseen_keeps_pages_listed_since_and_pinned_pages(tmp_path):
    cache = Cache(tmp_path / "c.db")
    gone, pinned = page("gone", "Gone"), page("pinned", "Pinned")
    cache.upsert_pages([gone, pinned])
    cache.put_content(gone, "budget")
    cache.set_pinned(pinned, True)
    listed = time.time()
    cache.upsert_pages([page("kept", "Kept")])
    assert cache.forget_unseen(listed) == ["gone"]
    assert list(cache.content_containing("budget")) == []
    assert {p.id for p in cache.recent_pages()} == {"pinned", "kept"}


# ---------- the real app on a fake Notion ----------

@pytest.mark.indexer
def test_search_lists_title_matches_then_content_matches_with_snippets(tmp_path, monkeypatch):
    pages = [page("t", "Budget plan", "2026-01-01"), page("c", "Trip notes", "2026-02-01"), page("x", "Other")]
    bodies = {"t": "nothing here", "c": "flights, hotel and the budget for food", "x": "unrelated"}

    async def search(query="", cursor=None, page_size=50):
        return [p for p in pages if query.lower() in p.title.lower()], None  # titles only, like Notion

    async def get_markdown(page_id):
        return bodies[page_id]

    monkeypatch.setattr(ntn, "search", search)
    monkeypatch.setattr(ntn, "get_markdown", get_markdown)

    async def run():
        app = NotionTUI(Cache(tmp_path / "c.db"), Options())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            await app.workers.wait_for_complete()
            app.query_one("#search").value = "budget"
            await pilot.pause(0.6)
            await app.workers.wait_for_complete()
            await pilot.pause()
            items = list(app.query(PageItem))
            assert [i.page.id for i in items] == ["t", "c"]  # title match first, though edited earlier
            assert items[0].snippet is None
            text, start, end = items[1].snippet
            assert text[start:end] == "budget"

    asyncio.run(run())


@pytest.mark.indexer
def test_indexer_waits_and_retries_when_notion_rate_limits(tmp_path, monkeypatch):
    pages = [page("a", "A"), page("b", "B")]
    refusals = {"a": 1}  # Notion says "too many requests" once, for the first page

    async def search(query="", cursor=None, page_size=50):
        return [p for p in pages if query.lower() in p.title.lower()], None

    async def get_markdown(page_id):
        if refusals.get(page_id):
            refusals[page_id] -= 1
            raise ntn.RateLimited("429 Too Many Requests rate_limited")
        return f"text of {page_id}"

    monkeypatch.setattr(ntn, "search", search)
    monkeypatch.setattr(ntn, "get_markdown", get_markdown)
    monkeypatch.setattr("notion_tui.app.INDEX_WAIT", 0)

    async def run():
        cache = Cache(tmp_path / "c.db")
        app = NotionTUI(cache, Options())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            await app.workers.wait_for_complete()
            assert cache.unindexed_pages() == []
            assert {p.id for p, _ in cache.content_containing("text of")} == {"a", "b"}

    asyncio.run(run())


@pytest.mark.indexer
def test_indexer_skips_a_failing_page_until_its_next_edit(tmp_path, monkeypatch):
    pages = [page("bad", "Bad"), page("ok", "Ok", "2026-06-01")]  # the cursor rests on "ok": no preview of "bad"
    fetches: list[str] = []

    async def search(query="", cursor=None, page_size=50):
        found = [p for p in pages if query.lower() in p.title.lower()]
        return sorted(found, key=lambda p: p.last_edited, reverse=True), None  # newest first, like Notion

    async def get_markdown(page_id):
        fetches.append(page_id)
        if page_id == "bad":
            raise ntn.NtnError("unsupported block")
        return "fine"

    monkeypatch.setattr(ntn, "search", search)
    monkeypatch.setattr(ntn, "get_markdown", get_markdown)
    monkeypatch.setattr(NotionTUI, "prefetch", lambda self, *args: None)  # count the indexer's fetches only

    async def run():
        app = NotionTUI(Cache(tmp_path / "c.db"), Options())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            await app.workers.wait_for_complete()
            assert {"bad", "ok"} <= set(fetches)  # the preview of the top page fetches "ok" too
            fetches.clear()
            app.index_content()  # the next round
            await app.workers.wait_for_complete()
            assert fetches == []
            pages[0] = page("bad", "Bad", "2026-05-01")  # edited: tried again
            app.index_content()
            await app.workers.wait_for_complete()
            assert fetches == ["bad"]

    asyncio.run(run())


@pytest.mark.indexer
def test_later_title_batches_stay_above_content_matches(tmp_path, monkeypatch):
    first, second = page("t1", "Budget 2026", "2026-03-01"), page("t2", "Budget 2025", "2026-02-01")
    inside = page("c", "Trip notes", "2026-04-01")
    bodies = {"t1": "x", "t2": "y", "c": "the budget for food"}

    async def search(query="", cursor=None, page_size=50):
        if not query:
            return [inside, first, second], None
        return ([first], "next") if cursor is None else ([second], None)  # titles only, in two batches

    async def get_markdown(page_id):
        return bodies[page_id]

    monkeypatch.setattr(ntn, "search", search)
    monkeypatch.setattr(ntn, "get_markdown", get_markdown)

    async def run():
        app = NotionTUI(Cache(tmp_path / "c.db"), Options())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            await app.workers.wait_for_complete()
            app.query_one("#search").value = "budget"
            await pilot.pause(0.6)
            await app.workers.wait_for_complete()
            await pilot.pause(0.3)  # the cursor near the end asks for the next batch
            await app.workers.wait_for_complete()
            await pilot.pause()
            items = list(app.query(PageItem))
            assert [i.page.id for i in items] == ["t1", "t2", "c"]
            assert [i.snippet is not None for i in items] == [False, False, True]

    asyncio.run(run())
