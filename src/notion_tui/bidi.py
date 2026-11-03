"""Right-to-left text (Hebrew, Arabic) in the preview.

Characters are left in logical order: kitty (and VTE terminals) reorder RTL text themselves,
so reordering here would reverse it a second time. What the terminal can't know is a
paragraph's direction, so RTL paragraphs and headings are right-aligned here, as in Notion.
"""

from __future__ import annotations

import re
import unicodedata

from markdown_it import MarkdownIt
from textual.widgets import Markdown
from textual.widgets._markdown import MarkdownBlock

_RTL_CHARS = re.compile("[֐-ࣿיִ-﷿ﹰ-ﻼ]")


def is_rtl(text: str) -> bool:
    """Paragraph direction (Unicode bidi rule P2): the first strong letter decides; LTR when none."""
    if not _RTL_CHARS.search(text):
        return False
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind == "L":
            return False
        if kind in ("R", "AL"):
            return True
    return False


class _RtlAware:
    """Mixed into Markdown blocks: tags the block `rtl` when its text is right-to-left."""

    def set_content(self, content) -> None:
        super().set_content(content)  # type: ignore[misc]
        self.set_class(is_rtl(getattr(content, "plain", str(content))), "rtl")  # type: ignore[attr-defined]


_rtl_blocks: dict[type, type] = {}


def _hard_breaks(state) -> None:
    for token in state.tokens:
        for child in token.children or ():
            if child.type == "softbreak":
                child.type = "hardbreak"


def _parser() -> MarkdownIt:
    # Notion writes one block per line, separated by single newlines; keep them as separate lines.
    md = MarkdownIt("gfm-like")
    md.core.ruler.push("hard_breaks", _hard_breaks)
    return md


class BidiMarkdown(Markdown):
    """Markdown that right-aligns RTL blocks, keeps Notion's single line breaks and colours links."""

    DEFAULT_CSS = """
    BidiMarkdown .rtl { text-align: right; }
    BidiMarkdown MarkdownBlock { link-color: $text-accent; link-style: underline; link-color-hover: $accent; }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("parser_factory", _parser)
        super().__init__(*args, **kwargs)

    def get_block_class(self, block_name: str) -> type[MarkdownBlock]:
        cls = super().get_block_class(block_name)
        if cls not in _rtl_blocks:
            _rtl_blocks[cls] = type(cls.__name__, (_RtlAware, cls), {})
        return _rtl_blocks[cls]
