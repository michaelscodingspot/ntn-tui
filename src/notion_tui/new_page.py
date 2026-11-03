"""New page: where it can go in the tree, and keeping it listed until Notion's search finds it.

Pure helpers for the new-page dialog in modals.py and the app. Tested in tests/test_new_page.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ntn import Page

Ancestry = tuple[list[str], tuple[str, str] | None]  # Cache.ancestry(): titles top first, first uncached step


@dataclass(frozen=True)
class Location:
    """Where a new page goes: inside a page, database or data source, or at the top level."""

    label: str  # "Inside: Notes / Quick Notes"
    parent_kind: str  # page | database | data_source | workspace
    parent_id: str  # "" for the workspace

    @classmethod
    def inside(cls, page: Page, path: str) -> Location:
        """Inside a list item; a database is shown as its data source, so it gets the rows' parent."""
        kind = "data_source" if page.kind == "data_source" else "page"
        return cls(f"Inside: {path}", kind, page.id)


def page_path(page: Page, ancestry: Ancestry) -> str:
    """"Notes / Quick Notes" from the cached ancestry; "… / Quick Notes" when it isn't all cached."""
    titles, missing = ancestry
    if missing is not None and page.parent_kind != "workspace":
        titles = ["…"]
    return " / ".join([*titles, page.title])


TOP_LEVEL = Location("Top level of the workspace", "workspace", "")


def nearby_locations(page: Page | None, ancestry: Ancestry) -> list[Location]:
    """Where a new page can go: inside the highlighted page, next to it, and always the top level."""
    if page is None:
        return [TOP_LEVEL]
    locations = [Location.inside(page, page_path(page, ancestry))]
    if page.parent_kind in ("page", "database", "data_source"):
        titles, missing = ancestry
        where = " / ".join(titles) if missing is None and titles else "its parent"
        locations.append(Location(f"Next to “{page.title}”: in {where}", page.parent_kind, page.parent_id))
    return [*locations, TOP_LEVEL]


@dataclass(frozen=True)
class NewPage:
    title: str
    location: Location


def with_unindexed(
    pages: list[Page], created: Page | None, query: str, created_during: str = ""
) -> tuple[list[Page], Page | None]:
    """Search results plus a page just created here, which Notion's search lists only after a few seconds.

    Returns the list to show and the page still to keep (None once search lists it). The page is
    added on the recent list, on the search it was created during, and on a search whose text is
    in its title.
    """
    if created is None:
        return pages, None
    if any(p.id == created.id for p in pages):
        return pages, None
    if query and query != created_during and query.lower() not in created.title.lower():
        return pages, created
    return [created, *pages], created
