"""New page: parent choices near the highlighted page, and the v1/pages request body."""

from notion_tui.modals import Location, nearby_locations, page_path
from notion_tui.ntn import Page, new_page_body

COMPLETE = (["Notes"], None)
UNCACHED = ([], ("page", "p0"))


def page(kind="page", parent_kind="page", parent_id="p0", title="Quick Notes"):
    return Page("p1", title, "", "", parent_kind=parent_kind, parent_id=parent_id, kind=kind)


def test_path_from_cache():
    assert page_path(page(), COMPLETE) == "Notes / Quick Notes"


def test_path_marks_uncached_ancestors():
    assert page_path(page(), UNCACHED) == "… / Quick Notes"


def test_path_of_top_level_page_has_no_marker():
    assert page_path(page(parent_kind="workspace", parent_id=""), UNCACHED) == "Quick Notes"


def test_nothing_highlighted():
    assert nearby_locations(None, COMPLETE) == []


def test_inside_and_next_to_a_page():
    assert nearby_locations(page(), COMPLETE) == [
        Location("Inside: Notes / Quick Notes", "page", "p1"),
        Location("Next to “Quick Notes”: in Notes", "page", "p0"),
    ]


def test_next_to_uncached_parent():
    assert nearby_locations(page(), UNCACHED)[1].label == "Next to “Quick Notes”: in its parent"


def test_database_row_goes_next_to_in_the_database():
    assert nearby_locations(page(parent_kind="database", parent_id="db"), COMPLETE)[1].parent_kind == "database"


def test_top_level_and_block_children_offer_only_inside():
    for parent_kind in ("workspace", "block"):
        assert len(nearby_locations(page(parent_kind=parent_kind), COMPLETE)) == 1


def test_database_is_entered_through_its_data_source():
    db = page(kind="data_source", parent_kind="workspace", parent_id="", title="Tasks")
    assert nearby_locations(db, ([], None))[0] == Location("Inside: Tasks", "data_source", "p1")


def test_body_per_parent_kind():
    for kind, key in (("page", "page_id"), ("database", "database_id"), ("data_source", "data_source_id")):
        assert new_page_body("T", kind, "x")["parent"] == {"type": key, key: "x"}


def test_body_title_is_plain_text():
    title = 'Plan: "Q4" — שלום'
    prop = new_page_body(title, "page", "x")["properties"]["title"]
    assert prop == {"title": [{"type": "text", "text": {"content": title}}]}
