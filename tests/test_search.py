import asyncio

from notion_tui import ntn
from notion_tui.app import NotionTUI, PageItem
from notion_tui.cache import Cache
from notion_tui.ntn import Page
from notion_tui.options import Options
from notion_tui.search import plain_text, snippet, title_matches, with_content_matches


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


def test_snippet_near_the_start_has_no_ellipsis():
    assert snippet("budget first", "budget") == ("budget first", 0, 6)


def test_snippet_none_when_only_a_link_target_matches():
    assert snippet("see [the doc](https://budget.example)", "budget") is None
    assert snippet("anything", "  ") is None


def test_title_matches_come_first():
    a, b, c = page("a", "Budget 2027"), page("b", "Notes"), page("c", "Trips")
    titles = title_matches([a, b, c], "budget")
    assert titles == [a]
    assert with_content_matches(titles, [b, a, c]) == [a, b, c]


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


# ---------- the real app on a fake Notion ----------

INDEX_CONTENT = NotionTUI.index_content  # captured before conftest turns the indexer off


def test_search_lists_title_matches_then_content_matches_with_snippets(tmp_path, monkeypatch):
    pages = [page("t", "Budget plan", "2026-01-01"), page("c", "Trip notes", "2026-02-01"), page("x", "Other")]
    bodies = {"t": "nothing here", "c": "flights, hotel and the budget for food", "x": "unrelated"}

    async def search(query="", cursor=None, page_size=50):
        return [p for p in pages if query.lower() in p.title.lower()], None  # titles only, like Notion

    async def get_markdown(page_id):
        return bodies[page_id]

    monkeypatch.setattr(ntn, "search", search)
    monkeypatch.setattr(ntn, "get_markdown", get_markdown)
    monkeypatch.setattr(NotionTUI, "index_content", INDEX_CONTENT)

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
