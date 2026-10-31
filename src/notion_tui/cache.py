"""Local SQLite cache so the app starts instantly and previews appear without waiting on ntn.

Lives at $XDG_CACHE_HOME/notion-tui/cache.db (default ~/.cache/notion-tui/cache.db).
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .ntn import Node, Page

MAX_DEPTH = 12  # guard against cycles or absurdly deep trees

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    last_edited TEXT NOT NULL,
    url         TEXT NOT NULL,
    seen_at     REAL NOT NULL
    -- kind     TEXT: page | data_source (added by migration)
    -- has_body INTEGER: 1/0 = page has / has no content blocks, NULL = not checked since last edit
);
CREATE INDEX IF NOT EXISTS pages_last_edited ON pages(last_edited DESC);

CREATE TABLE IF NOT EXISTS page_content (
    page_id          TEXT PRIMARY KEY,
    markdown         TEXT NOT NULL,
    page_last_edited TEXT NOT NULL,  -- page's last_edited when this markdown was fetched
    fetched_at       REAL NOT NULL
);

-- Pages, databases, data sources and blocks seen as ancestors, for the breadcrumb path.
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,  -- page | database | data_source | block
    title       TEXT NOT NULL,  -- '' when the step has no name of its own
    parent_kind TEXT NOT NULL,  -- 'workspace' at the top
    parent_id   TEXT NOT NULL,
    fetched_at  REAL NOT NULL
);
"""


def default_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "notion-tui" / "cache.db"


class Cache:
    def __init__(self, path: Path | None = None) -> None:
        path = path or default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(pages)")}
        with self.db:
            if "kind" not in columns:  # caches made before databases were listed
                self.db.execute("ALTER TABLE pages ADD COLUMN kind TEXT NOT NULL DEFAULT 'page'")
            if "has_body" not in columns:  # caches made before the database entries option
                self.db.execute("ALTER TABLE pages ADD COLUMN has_body INTEGER")

    def recent_pages(self, limit: int = 200) -> list[Page]:
        rows = self.db.execute(
            "SELECT p.id, p.title, p.last_edited, p.url, COALESCE(n.parent_kind, ''), COALESCE(n.parent_id, ''), p.kind "
            "FROM pages p LEFT JOIN nodes n ON n.id = p.id ORDER BY p.last_edited DESC LIMIT ?",
            (limit,),
        )
        return [Page(*row) for row in rows]

    def upsert_pages(self, pages: list[Page]) -> None:
        now = time.time()
        with self.db:
            self.db.executemany(
                "INSERT INTO pages (id, title, last_edited, url, seen_at, kind) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, last_edited=excluded.last_edited, "
                "url=excluded.url, seen_at=excluded.seen_at, kind=excluded.kind, "
                # An edit may add or remove content: check the body again.
                "has_body=CASE WHEN pages.last_edited = excluded.last_edited THEN pages.has_body END",
                [(p.id, p.title, p.last_edited, p.url, now, p.kind) for p in pages],
            )
        self.put_nodes([Node(p.id, p.kind, p.title, p.parent_kind, p.parent_id) for p in pages if p.parent_kind])

    def has_body(self, page_ids: list[str]) -> dict[str, bool | None]:
        """Whether each page has content blocks; None when not checked since its last edit."""
        found: dict[str, bool | None] = dict.fromkeys(page_ids)
        for start in range(0, len(page_ids), 500):
            chunk = page_ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for page_id, value in self.db.execute(f"SELECT id, has_body FROM pages WHERE id IN ({marks})", chunk):
                found[page_id] = None if value is None else bool(value)
        return found

    def put_has_body(self, page_id: str, has_body: bool) -> None:
        with self.db:
            self.db.execute("UPDATE pages SET has_body = ? WHERE id = ?", (int(has_body), page_id))

    def put_nodes(self, nodes: list[Node]) -> None:
        now = time.time()
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO nodes (id, kind, title, parent_kind, parent_id, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(n.id, n.kind, n.title, n.parent_kind, n.parent_id, now) for n in nodes],
            )

    def ancestry(self, page_id: str) -> tuple[list[str], tuple[str, str] | None]:
        """Titles of the page's ancestors, top first, and the first (kind, id) not cached yet (None if complete).

        When something is missing, the titles are only the cached part just above the page.
        """
        titles: list[str] = []
        kind, node_id = "page", page_id  # start at the page itself: its row holds its parent
        for depth in range(MAX_DEPTH):
            row = self.db.execute("SELECT title, parent_kind, parent_id FROM nodes WHERE id = ?", (node_id,)).fetchone()
            if row is None:
                return titles[::-1], (kind, node_id)
            title, kind, node_id = row
            if depth and title:
                titles.append(title)
            if kind == "workspace" or not node_id:
                break
        return titles[::-1], None

    def content(self, page_id: str) -> tuple[str, str] | None:
        """(markdown, page_last_edited) or None."""
        return self.db.execute(
            "SELECT markdown, page_last_edited FROM page_content WHERE page_id = ?", (page_id,)
        ).fetchone()

    def is_fresh(self, page: Page) -> bool:
        cached = self.content(page.id)
        return cached is not None and cached[1] == page.last_edited

    def put_content(self, page: Page, markdown: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO page_content (page_id, markdown, page_last_edited, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (page.id, markdown, page.last_edited, time.time()),
            )

    def invalidate(self, page_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM page_content WHERE page_id = ?", (page_id,))

    def forget(self, page_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM page_content WHERE page_id = ?", (page_id,))
            self.db.execute("DELETE FROM pages WHERE id = ?", (page_id,))
            self.db.execute("DELETE FROM nodes WHERE id = ?", (page_id,))
