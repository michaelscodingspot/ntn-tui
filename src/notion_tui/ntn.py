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


def _title(obj: dict) -> str:
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            text = "".join(part.get("plain_text", "") for part in prop.get("title", []))
            return text.strip() or "(untitled)"
    return "(untitled)"


def _page(obj: dict) -> Page:
    return Page(
        id=obj["id"],
        title=_title(obj),
        last_edited=obj.get("last_edited_time", ""),
        url=obj.get("url", ""),
    )


async def search(query: str = "", cursor: str | None = None, page_size: int = 50) -> tuple[list[Page], str | None]:
    """One page of search results. Empty query = most recently edited first.

    Returns the pages and the cursor for the next batch (None when exhausted).
    """
    body: dict = {"filter": {"property": "object", "value": "page"}, "page_size": page_size}
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
        if r.get("object") == "page" and not r.get("in_trash") and not r.get("archived")
    ]
    return pages, data.get("next_cursor") if data.get("has_more") else None


async def get_markdown(page_id: str) -> str:
    return await _run("pages", "get", page_id)


async def trash(page_id: str) -> None:
    await _run("pages", "trash", page_id, "--yes")


def run_interactive(*args: str) -> int:
    """Run ntn attached to the real terminal (it opens $EDITOR). Call while the TUI is suspended."""
    return subprocess.call([NTN, *args])
