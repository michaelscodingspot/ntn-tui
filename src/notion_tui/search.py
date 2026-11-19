"""Pure page-search helpers: content matches next to title matches, and the snippet shown under a match.

Notion's search API matches titles only, so text inside pages is searched in the local cache
(`page_content`), which the app fills in the background.
"""

from __future__ import annotations

import re

from .ntn import Page

SNIPPET_BEFORE = 20  # characters of context kept before the match

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


def snippet(markdown: str, query: str) -> tuple[str, int, int] | None:
    """The text around the first match of `query` in a page, for the line under its title:
    (text, match start, match end) in that text, or None when the page does not contain it."""
    needle = query.strip().lower()
    if not needle:
        return None
    text = plain_text(markdown)
    at = text.lower().find(needle)
    if at < 0:
        return None
    start = max(0, at - SNIPPET_BEFORE)
    if start:
        space = text.find(" ", start, at)  # start on a word
        start = space + 1 if space >= 0 else start
    prefix = "…" if start else ""
    shown = prefix + text[start:]
    begin = len(prefix) + at - start
    return shown, begin, begin + len(needle)


def title_matches(pages: list[Page], query: str) -> list[Page]:
    needle = query.strip().lower()
    return [p for p in pages if needle in p.title.lower()]


def with_content_matches(title_hits: list[Page], content_hits: list[Page]) -> list[Page]:
    """Title matches first, in their order, then pages matched only inside, in theirs."""
    ids = {p.id for p in title_hits}
    return title_hits + [p for p in content_hits if p.id not in ids]
