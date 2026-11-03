from notion_tui.app import same_rows, vanished, with_cached_tail
from notion_tui.ntn import Page


def page(id: str, edited: str) -> Page:
    return Page(id, id, edited, "")


def test_with_cached_tail_keeps_only_older_unlisted_pages():
    fresh = [page("a", "2026-11-03T10:00:00.000Z"), page("b", "2026-11-02T10:00:00.000Z")]
    cached = [
        page("a", "2026-11-01T00:00:00.000Z"),  # stale copy of a fresh page: dropped
        page("c", "2026-11-02T12:00:00.000Z"),  # newer than the batch's oldest: Notion's batch is the truth
        page("d", "2026-11-01T10:00:00.000Z"),
        page("e", "2026-10-01T10:00:00.000Z"),
    ]
    assert [p.id for p in with_cached_tail(fresh, cached)] == ["a", "b", "d", "e"]


def test_with_cached_tail_empty_batch():
    assert with_cached_tail([], [page("x", "2026-01-01T00:00:00.000Z")]) == []


def test_vanished_drops_only_pages_the_batch_proves_gone():
    fresh = [page("a", "2026-11-03T10:00:00.000Z"), page("b", "2026-11-02T10:00:00.000Z")]
    cached = [
        page("a", "2026-11-03T10:00:00.000Z"),
        page("t", "2026-11-02T12:00:00.000Z"),  # newer than the batch's oldest, missing: trashed
        page("s", "2026-11-02T10:00:00.000Z"),  # same minute as the oldest: may be in the next batch
        page("o", "2026-11-01T10:00:00.000Z"),  # older: not covered by this batch
    ]
    assert [p.id for p in vanished(fresh, cached, complete=False)] == ["t"]
    assert [p.id for p in vanished(fresh, cached, complete=True)] == ["t", "s", "o"]
    assert vanished([], cached, complete=False) == []


def test_same_rows_when_fresh_data_matches_the_cache():
    assert same_rows([page("a", "2026-01-02"), page("b", "2026-01-01")], [page("a", "2026-01-02"), page("b", "2026-01-01")])


def test_rows_differ_on_order_length_or_edit_time():
    a, b = page("a", "2026-01-02"), page("b", "2026-01-01")
    assert not same_rows([a, b], [b, a])
    assert not same_rows([a, b], [a])
    assert not same_rows([a], [page("a", "2026-01-03")])
