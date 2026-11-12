"""Async wrapper around the `ntn` Notion CLI.

The TUI never talks to the Notion API directly: every call goes through `ntn`,
which owns authentication and API versioning.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import unquote

from .query import QueryError, combine

NTN = shutil.which("ntn") or "ntn"


class NtnError(RuntimeError):
    """`ntn` exited with a non-zero status."""


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    last_edited: str  # ISO-8601, as returned by Notion
    url: str
    parent_kind: str = ""  # "" = unknown, "workspace" = top level, else a Node kind
    parent_id: str = ""
    kind: str = "page"  # page | data_source (a database's table of rows)

    @property
    def is_entry(self) -> bool:
        """A page that is a row of a database."""
        return self.kind == "page" and self.parent_kind in ("database", "data_source")


@dataclass(frozen=True)
class Node:
    """One step in a page's ancestry: a page, database, data source or block."""

    id: str
    kind: str  # page | database | data_source | block
    title: str  # "" for steps that have no name of their own (blocks, data sources)
    parent_kind: str  # "workspace" at the top
    parent_id: str


async def _run(*args: str, stdin: str | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        NTN,
        *args,
        stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await proc.communicate(None if stdin is None else stdin.encode())
    except asyncio.CancelledError:
        # A newer request superseded this one: don't leave ntn running.
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # it had already exited
        await proc.wait()
        raise
    if proc.returncode != 0:
        message = err.decode(errors="replace").strip()
        raise NtnError(message or f"ntn exited with status {proc.returncode}")
    return out.decode(errors="replace")


def _plain(parts: list[dict]) -> str:
    return "".join(part.get("plain_text", "") for part in parts or [])


def _title(obj: dict) -> str:
    if obj.get("object") in ("data_source", "database"):
        return _plain(obj.get("title")).strip() or "(untitled)"
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            text = "".join(part.get("plain_text", "") for part in prop.get("title", []))
            return text.strip() or "(untitled)"
    return "(untitled)"


def _parent(obj: dict) -> tuple[str, str]:
    """(kind, id) of an object's parent; ("workspace", "") at the top."""
    parent = obj.get("parent") or {}
    match parent.get("type"):
        case "page_id":
            return "page", parent["page_id"]
        case "database_id":
            return "database", parent["database_id"]
        case "data_source_id":
            # Skip the data source hop when Notion already tells us its database.
            if parent.get("database_id"):
                return "database", parent["database_id"]
            return "data_source", parent["data_source_id"]
        case "block_id":
            return "block", parent["block_id"]
    return "workspace", ""


def _page(obj: dict) -> Page:
    """A list item: a page, or a data source (shown as its database: same name, URL and place)."""
    is_table = obj.get("object") == "data_source"
    # A data source sits inside its database; the database's parent is where it lives in the tree.
    parent_kind, parent_id = _parent({"parent": obj.get("database_parent")} if is_table else obj)
    return Page(
        id=obj["id"],
        title=_title(obj),
        last_edited=obj.get("last_edited_time", ""),
        url=obj.get("url", ""),
        parent_kind=parent_kind,
        parent_id=parent_id,
        kind="data_source" if is_table else "page",
    )


async def search(query: str = "", cursor: str | None = None, page_size: int = 50) -> tuple[list[Page], str | None]:
    """One batch of search results: pages and databases. Empty query = most recently edited first.

    Returns the items and the cursor for the next batch (None when exhausted).
    """
    body: dict = {"page_size": page_size}
    if query:
        body["query"] = query  # Notion ranks by relevance
    else:
        body["sort"] = {"direction": "descending", "timestamp": "last_edited_time"}
    if cursor:
        body["start_cursor"] = cursor
    data = json.loads(await _run("api", "v1/search", "-d", json.dumps(body)))
    pages = [
        _page(r)
        for r in data.get("results", [])
        if r.get("object") in ("page", "data_source") and not r.get("in_trash") and not r.get("archived")
    ]
    return pages, data.get("next_cursor") if data.get("has_more") else None


async def get_page(page_id: str) -> Page:
    """A page's list item fresh from Notion: its title and last edit time."""
    return _page(json.loads(await _run("api", "v1/pages/" + page_id)))


_NODE_ENDPOINTS = {
    "page": "v1/pages/",
    "database": "v1/databases/",
    "data_source": "v1/data_sources/",
    "block": "v1/blocks/",
}


async def get_node(kind: str, node_id: str) -> Node:
    """Fetch one ancestor so its title and parent can be cached."""
    obj = json.loads(await _run("api", _NODE_ENDPOINTS[kind] + node_id))
    title = _title(obj) if kind in ("page", "database") else ""
    parent_kind, parent_id = _parent(obj)
    return Node(node_id, kind, title, parent_kind, parent_id)


@dataclass(frozen=True)
class View:
    """A saved view of a database: its filter, sorts and visible columns (read-only here)."""

    id: str
    name: str
    type: str  # table | board | list | calendar | timeline | gallery | form | chart | map | dashboard
    filter: dict | None  # same shape as a data-source query filter
    sorts: list | None  # same shape as query sorts; properties are property IDs
    columns: list | None  # [{"property_id", "visible"}] in display order; None when the view has none
    last_edited: str = ""


async def list_view_ids(data_source_id: str) -> list[str]:
    """IDs of a data source's views, in Notion's order (all pages)."""
    ids: list[str] = []
    cursor = None
    while True:
        path = f"v1/views?data_source_id={data_source_id}" + (f"&start_cursor={cursor}" if cursor else "")
        data = json.loads(await _run("api", path, "-X", "GET"))
        ids += [r["id"] for r in data.get("results", []) if r.get("id")]
        cursor = data.get("next_cursor")
        if not (data.get("has_more") and cursor):
            return ids


async def get_view(view_id: str) -> View:
    obj = json.loads(await _run("api", f"v1/views/{view_id}", "-X", "GET"))
    config = obj.get("configuration") or {}
    # Quick filters (the filter pills under a view's tabs) narrow the view too: AND them in.
    # A pill with no value chosen comes as {}: it filters nothing, and Notion refuses it in a query.
    quick = [{"property": prop_id, **condition} for prop_id, condition in (obj.get("quick_filters") or {}).items() if condition]
    try:
        view_filter = combine(obj.get("filter") or None, quick)
    except QueryError:
        view_filter = obj.get("filter") or None  # too deep for Notion with the quick filters ANDed in
    return View(
        id=obj.get("id", view_id),
        name=obj.get("name") or "",
        type=obj.get("type") or "table",
        filter=view_filter,
        sorts=obj.get("sorts") or None,
        columns=config.get("properties") or None,
        last_edited=obj.get("last_edited_time", ""),
    )


async def get_schema(data_source_id: str) -> list[dict]:
    """A data source's properties: [{id, name, type, options}], title first."""
    obj = json.loads(await _run("api", f"v1/data_sources/{data_source_id}", "-X", "GET"))
    props = []
    for name, prop in (obj.get("properties") or {}).items():
        kind = prop.get("type", "")
        options = [o.get("name", "") for o in (prop.get(kind) or {}).get("options", [])] if isinstance(prop.get(kind), dict) else []
        # The schema URL-encodes IDs ("%5Dkno") while views use them raw ("]kno").
        props.append({"id": unquote(prop.get("id", "")), "name": name, "type": kind, "options": options})
    props.sort(key=lambda p: p["type"] != "title")
    return props


async def query_rows(
    data_source_id: str,
    filter: dict | None = None,
    sorts: list | None = None,
    start_cursor: str | None = None,
    page_size: int = 100,
) -> tuple[list[dict], str | None]:
    """One page of a database's rows, and the cursor for the next page (None when there are no more)."""
    body: dict = {"page_size": page_size}
    if filter:
        body["filter"] = filter
    if sorts:
        body["sorts"] = sorts
    if start_cursor:
        body["start_cursor"] = start_cursor
    data = json.loads(await _run("api", f"v1/data_sources/{data_source_id}/query", "-d", json.dumps(body)))
    rows = [r for r in data.get("results", []) if not r.get("in_trash")]
    return rows, data.get("next_cursor") if data.get("has_more") else None


async def highest_number(data_source_id: str, name: str) -> float | None:
    """The highest value of a number column over all the database's rows; None when every row leaves it empty."""
    rows, _ = await query_rows(
        data_source_id,
        filter={"property": name, "number": {"is_not_empty": True}},
        sorts=[{"property": name, "direction": "descending"}],
        page_size=1,
    )
    return rows[0]["properties"][name].get("number") if rows else None


def value_text(prop: dict) -> str:
    """A row property as short plain text ("" when empty or not shown)."""
    kind = prop.get("type")
    value = prop.get(kind)
    match kind:
        case "title" | "rich_text":
            return _plain(value).strip()
        case "select" | "status":
            return (value or {}).get("name", "")
        case "multi_select":
            return ", ".join(o.get("name", "") for o in value or [])
        case "date":
            if not value:
                return ""
            start, end = value.get("start", "")[:10], (value.get("end") or "")[:10]
            return f"{start} → {end}" if end else start
        case "number":
            return "" if value is None else f"{value:g}"
        case "checkbox":
            return "✓" if value else ""
        case "url" | "email" | "phone_number":
            return value or ""
        case "people":
            return ", ".join(p.get("name", "") for p in value or [])
        case "created_time" | "last_edited_time":
            return (value or "")[:10]
        case "unique_id":
            if not value or value.get("number") is None:
                return ""
            return f"{value.get('prefix') or ''}{'-' if value.get('prefix') else ''}{value['number']}"
        case "formula":
            inner = (value or {}).get((value or {}).get("type"))
            return "" if inner is None else str(inner)
    return ""


_TEXT_BLOCKS = {"paragraph", "heading_1", "heading_2", "heading_3", "quote", "bulleted_list_item", "numbered_list_item"}


def _is_blank(block: dict) -> bool:
    """An empty line: a text block with no text and nothing nested inside."""
    kind = block.get("type")
    return kind in _TEXT_BLOCKS and not _plain(block.get(kind, {}).get("rich_text")).strip() and not block.get("has_children")


async def has_body(page_id: str, look_at: int = 25) -> bool:
    """Whether a page has content: at least one block that isn't an empty line (one ntn call)."""
    data = json.loads(await _run("api", f"v1/blocks/{page_id}/children", f"page_size=={look_at}"))
    blocks = data.get("results", [])
    # Past `look_at` blank lines, assume content follows rather than paging on.
    return any(not _is_blank(b) for b in blocks) or bool(data.get("has_more"))


async def get_markdown(page_id: str) -> str:
    return await _run("pages", "get", page_id)


async def get_body(page_id: str) -> tuple[str, bool]:
    """A page's Markdown body without the properties, as `ntn pages edit` puts it in the editor,
    and whether Notion truncated it."""
    out = await _run("pages", "get", page_id, "--json")
    try:
        data = json.loads(out)["markdown"]
        return data.get("markdown", ""), bool(data.get("truncated"))
    except (ValueError, KeyError, TypeError, AttributeError) as err:
        raise NtnError(f"Unexpected `ntn pages get --json` output: {err!r}") from err


async def set_body(page_id: str, markdown: str) -> None:
    """Replace a page's body with `markdown` (sent on stdin, so any size fits)."""
    await _run("pages", "edit", page_id, stdin=markdown)


_PARENT_KEYS = {"page": "page_id", "database": "database_id", "data_source": "data_source_id"}


def new_page_body(title: str, parent_kind: str, parent_id: str) -> dict:
    """`v1/pages` request body: an empty page with a title under a page, database, data source or the workspace."""
    if parent_kind == "workspace":
        parent = {"type": "workspace", "workspace": True}
    else:
        key = _PARENT_KEYS[parent_kind]
        parent = {"type": key, key: parent_id}
    return {
        "parent": parent,
        # "title" is the title property's ID in every database, and the only property of a plain page.
        "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
    }


async def create_page(title: str, parent_kind: str, parent_id: str) -> Page:
    """Create an empty titled page; returns it as a list item."""
    body = new_page_body(title, parent_kind, parent_id)
    return _page(json.loads(await _run("api", "v1/pages", "-d", json.dumps(body))))


async def trash(page_id: str) -> None:
    await _run("pages", "trash", page_id, "--yes")


@dataclass(frozen=True)
class Database:
    """The database a listed data source belongs to. Trashing it takes all its data sources and rows along."""

    id: str
    data_source_ids: tuple[str, ...]

    @property
    def ids(self) -> set[str]:
        return {self.id, *self.data_source_ids}


async def database_of(data_source_id: str) -> Database:
    """The database holding a data source (two ntn calls: the list only knows the data source's ID)."""
    try:
        source = json.loads(await _run("api", f"v1/data_sources/{data_source_id}", "-X", "GET"))
        database_id = source["parent"]["database_id"]
        database = json.loads(await _run("api", f"v1/databases/{database_id}", "-X", "GET"))
        return Database(database_id, tuple(s["id"] for s in database["data_sources"]))
    except (ValueError, KeyError, TypeError) as err:
        raise NtnError(f"Unexpected `ntn api` output for a database: {err!r}") from err


async def trash_database(database_id: str) -> None:
    await _run("api", f"v1/databases/{database_id}", "-X", "PATCH", "-d", json.dumps({"in_trash": True}))


def _object(out: str) -> dict:
    try:
        return json.loads(out)
    except ValueError as err:
        raise NtnError(f"Unexpected `ntn api` output: {err!r}") from err


async def create_row(data_source_id: str, title: str, properties: dict | None = None) -> dict:
    """Add a row with a title (and other property values, {name: value}) to a database;
    returns the row as a query returns it."""
    body = new_page_body(title, "data_source", data_source_id)
    body["properties"] |= properties or {}
    return _object(await _run("api", "v1/pages", "-d", json.dumps(body)))


async def update_row(row_id: str, properties: dict) -> dict:
    """Change a row's properties ({name: value}); returns the whole row as Notion now has it."""
    body = json.dumps({"properties": properties})
    return _object(await _run("api", f"v1/pages/{row_id}", "-X", "PATCH", "-d", body))


async def restore(page_id: str) -> None:
    """Bring a page or row back from the trash."""
    await _run("api", f"v1/pages/{page_id}", "-X", "PATCH", "-d", json.dumps({"in_trash": False}))


def row_title(row: dict) -> str:
    return _title(row)


def row_page(row: dict) -> Page:
    """A database row as a Page, for what works on pages (editing its body)."""
    return _page(row)


def run_interactive(*args: str) -> int:
    """Run ntn attached to the real terminal (it opens $EDITOR). Call while the TUI is suspended."""
    return subprocess.call([NTN, *args])
