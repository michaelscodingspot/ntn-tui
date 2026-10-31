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


async def _run(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        NTN,
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        # A newer request superseded this one: don't leave ntn running.
        proc.kill()
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


async def query_rows(data_source_id: str, page_size: int = 100) -> tuple[list[dict], bool]:
    """First rows of a database table, and whether there are more."""
    data = json.loads(await _run("api", f"v1/data_sources/{data_source_id}/query", "-d", json.dumps({"page_size": page_size})))
    rows = [r for r in data.get("results", []) if not r.get("in_trash")]
    return rows, bool(data.get("has_more"))


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


async def trash(page_id: str) -> None:
    await _run("pages", "trash", page_id, "--yes")


def run_interactive(*args: str) -> int:
    """Run ntn attached to the real terminal (it opens $EDITOR). Call while the TUI is suspended."""
    return subprocess.call([NTN, *args])
