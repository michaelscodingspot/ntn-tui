"""Links in a page's Markdown: Notion's link tags made into Markdown links, the list of a
page's links and images for the link picker, and the Notion ID inside a Notion URL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

# Notion's page and database links: <page url="…">Title</page>, <database url="…" …>Title</database>
_NOTION_TAG = re.compile(r'<(page|database)\b[^>]*?\burl="([^"]+)"[^>]*>(.*?)</\1>', re.S)
_NOTION_HOSTS = ("notion.so", "notion.com", "notion.site")
_ID = re.compile(r"([0-9a-f]{32})$", re.I)


@dataclass(frozen=True)
class Link:
    title: str
    url: str
    image: bool = False


def notion_links_to_markdown(md: str) -> str:
    """Turn Notion's page and database link tags into Markdown links."""

    def link(match: re.Match[str]) -> str:
        url, title = match.group(2), match.group(3).strip() or "Untitled"
        title = re.sub(r"([\\\[\]])", r"\\\1", title)
        return f"[{title}]({url.replace(' ', '%20').replace(')', '%29')})"

    return _NOTION_TAG.sub(link, md)


def page_links(md: str) -> list[Link]:
    """Every link in the Markdown, in order, each URL once; then every image."""
    links: dict[str, Link] = {}
    images: dict[str, Link] = {}
    for token in MarkdownIt("gfm-like").parse(md):
        children = token.children or []
        for i, child in enumerate(children):
            if child.type == "image":
                url = str(child.attrs.get("src", ""))
                if url and url not in images:
                    name = child.content.strip() or file_name(url)
                    images[url] = Link(f"Image {len(images) + 1}" + (f" · {name}" if name else ""), url, image=True)
                continue
            if child.type != "link_open":
                continue
            url = str(child.attrs.get("href", ""))
            text = []
            for inner in children[i + 1:]:
                if inner.type == "link_close":
                    break
                text.append(inner.content)
            if url and url not in links:
                links[url] = Link("".join(text).strip() or url, url)
    return [*links.values(), *images.values()]


def file_name(url: str) -> str:
    """The last part of the URL's path, decoded: "image.png"."""
    return unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])


def same_file(a: str, b: str) -> bool:
    """Whether two URLs name the same file, ignoring the query (Notion's file links are
    signed and expire after an hour; a fresh copy differs only in the query)."""
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.netloc, pa.path) == (pb.netloc, pb.path)


def notion_id(url: str) -> str | None:
    """The page or database ID (32 hex digits, no dashes) a Notion URL points at; None for other URLs."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not any(host == h or host.endswith("." + h) for h in _NOTION_HOSTS):
        return None
    match = _ID.search(parts.path.rstrip("/").replace("-", ""))
    return match.group(1).lower() if match else None
