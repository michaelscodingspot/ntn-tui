"""Pure page-search helpers: content matches next to title matches, and the snippet shown under a match.

Notion's search API matches titles only, so text inside pages is searched in the local cache
(`page_content`), which the app fills in the background.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .ntn import Page

SNIPPET_BEFORE = 20  # characters of context kept before the match
SNIPPET_AFTER = 200  # characters kept after the match: more than a list row shows
CONTENT_LIMIT = 300  # most pages listed as matched inside

Snippet = tuple[str, int, int]  # text, match start, match end

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_TAG = re.compile(r"<[^>\n]*>")  # Notion's <page>, <empty-block/>, <br> and similar
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")  # [text](url) → text
_MARKS = re.compile(r"[*`#>|~]+|^\s*[-+]\s+(\[[ x]\]\s+)?", re.M)
_SPACE = re.compile(r"\s+")


def plain_text(markdown: str) -> str:
    """Page Markdown as one line of searchable words: properties and body, no tags, link targets or Markdown marks."""
    match = _FRONTMATTER.match(markdown)
    if match:
        markdown = match.group(1) + "\n" + markdown[match.end():]
    text = _TAG.sub(" ", markdown)
    text = _LINK.sub(r"\1", text)
    text = _MARKS.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def snippet(markdown: str, query: str) -> Snippet | None:
    """The text around the first match of `query` in a page, for the line under its title:
    (text, match start, match end) in that text, or None when the page does not contain it."""
    needle = query.strip()
    if not needle:
        return None
    text = plain_text(markdown)
    match = re.search(re.escape(needle), text, re.I)  # offsets in `text` itself, even where lowercasing changes length
    if not match:
        return None
    at, end = match.span()
    start = max(0, at - SNIPPET_BEFORE)
    if start:
        space = text.find(" ", start, at)  # start on a word
        start = space + 1 if space >= 0 else start
    prefix = "…" if start else ""
    shown = prefix + text[start:end + SNIPPET_AFTER]
    shift = len(prefix) - start
    return shown, at + shift, end + shift


def title_matches(pages: list[Page], query: str) -> list[Page]:
    needle = query.strip().lower()
    return [p for p in pages if needle in p.title.lower()]


def content_matches(rows: Iterable[tuple[Page, str]], query: str, limit: int = CONTENT_LIMIT) -> list[tuple[Page, Snippet]]:
    """Pages whose text contains `query`, with their snippets, from (page, Markdown) rows that matched raw.
    The raw Markdown may match only inside a link target or tag: those are left out, and do not count toward `limit`."""
    found: list[tuple[Page, Snippet]] = []
    for page, markdown in rows:
        line = snippet(markdown, query)
        if line:
            found.append((page, line))
            if len(found) >= limit:
                break
    return found


def with_content_matches(
    title_hits: list[Page], content: list[tuple[Page, Snippet]]
) -> tuple[list[Page], dict[str, Snippet]]:
    """Title matches first, in their order, then pages matched only inside, in theirs, and those pages' snippets
    (a title match shows no snippet)."""
    ids = {p.id for p in title_hits}
    inside = [(p, line) for p, line in content if p.id not in ids]
    return title_hits + [p for p, _ in inside], {p.id: line for p, line in inside}
