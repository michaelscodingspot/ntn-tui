"""Local SQLite cache so the app starts instantly and previews appear without waiting on ntn.

Lives at $XDG_CACHE_HOME/notion-tui/cache.db (default ~/.cache/notion-tui/cache.db).
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .ntn import Page

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    last_edited TEXT NOT NULL,
    url         TEXT NOT NULL,
    seen_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS pages_last_edited ON pages(last_edited DESC);

CREATE TABLE IF NOT EXISTS page_content (
    page_id          TEXT PRIMARY KEY,
    markdown         TEXT NOT NULL,
    page_last_edited TEXT NOT NULL,  -- page's last_edited when this markdown was fetched
    fetched_at       REAL NOT NULL
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

    def recent_pages(self, limit: int = 200) -> list[Page]:
        rows = self.db.execute(
            "SELECT id, title, last_edited, url FROM pages ORDER BY last_edited DESC LIMIT ?", (limit,)
        )
        return [Page(*row) for row in rows]

    def upsert_pages(self, pages: list[Page]) -> None:
        now = time.time()
        with self.db:
            self.db.executemany(
                "INSERT INTO pages (id, title, last_edited, url, seen_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, last_edited=excluded.last_edited, "
                "url=excluded.url, seen_at=excluded.seen_at",
                [(p.id, p.title, p.last_edited, p.url, now) for p in pages],
            )

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
