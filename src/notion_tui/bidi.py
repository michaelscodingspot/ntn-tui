"""Right-to-left text (Hebrew, Arabic) on screen.

kitty draws the letters of each RTL word in order (it shapes every run of RTL letters), but it
does not reorder the words: "רשת אקווריום" shows with its first word on the left. So each
rendered line is put into visual order here (Unicode bidi algorithm, after Textual wraps it),
and then every run of RTL letters is turned back to logical order for kitty to draw. Net
effect: words and numbers move, letters inside a word don't. RTL preview paragraphs and
headings are right-aligned, as in Notion.

The bidi part is a compact UAX #9 for one embedding level: no explicit embeddings or isolates,
which Notion text practically never contains.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from markdown_it import MarkdownIt
from rich.segment import Segment
from textual.app import ComposeResult
from textual.strip import Strip
from textual.widgets import Markdown
from textual.widgets._markdown import MarkdownBlock, MarkdownTable, MarkdownTableCellContents, MarkdownTableContent

_RTL_CHARS = re.compile("[֐-ࣿיִ-﷿ﹰ-ﻼ\U00010800-\U00010fff\U0001e800-\U0001efff]")
_MIRROR = dict(zip("()[]{}<>«»", ")(][}{><»«"))
_NEUTRAL = {"ON", "WS", "S", "B", "BN"}


def has_rtl(text: str) -> bool:
    return _RTL_CHARS.search(text) is not None


def is_rtl(text: str) -> bool:
    """Paragraph direction (UAX #9 P2/P3): the first strong character decides; LTR when none."""
    if not has_rtl(text):
        return False
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind == "L":
            return False
        if kind in ("R", "AL"):
            return True
    return False


@lru_cache(maxsize=4096)
def _kind(ch: str) -> str:
    kind = unicodedata.bidirectional(ch) or "L"
    if kind in ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"):
        return "BN"
    return kind


def _levels(text: str, rtl: bool) -> list[int]:
    base = 1 if rtl else 0
    sos = "R" if rtl else "L"
    types = [_kind(ch) for ch in text]
    n = len(types)

    # W1: a non-spacing mark takes the type of what it follows.
    prev = sos
    for i, t in enumerate(types):
        if t == "NSM":
            types[i] = prev
        elif t != "BN":
            prev = t
    # W2: a European number after Arabic letters is an Arabic number. W3: AL becomes R.
    strong = sos
    for i, t in enumerate(types):
        if t in ("L", "R", "AL"):
            strong = t
        elif t == "EN" and strong == "AL":
            types[i] = "AN"
    types = ["R" if t == "AL" else t for t in types]
    # W4: one separator between two numbers of the same type joins them.
    for i in range(1, n - 1):
        t, before, after = types[i], types[i - 1], types[i + 1]
        if t == "ES" and before == after == "EN":
            types[i] = "EN"
        elif t == "CS" and before == after and before in ("EN", "AN"):
            types[i] = before
    # W5: terminators (%, $, #...) touching a European number become part of it.
    i = 0
    while i < n:
        if types[i] == "ET":
            j = i
            while j < n and types[j] == "ET":
                j += 1
            if (i > 0 and types[i - 1] == "EN") or (j < n and types[j] == "EN"):
                types[i:j] = ["EN"] * (j - i)
            i = j
        else:
            i += 1
    # W6: leftover separators and terminators are neutral. W7: EN after L (or LTR start) is L.
    types = ["ON" if t in ("ES", "ET", "CS") else t for t in types]
    strong = sos
    for i, t in enumerate(types):
        if t in ("L", "R"):
            strong = t
        elif t == "EN" and strong == "L":
            types[i] = "L"
    # N1/N2: a neutral run between two same-direction sides takes it; otherwise the base direction.
    i = 0
    while i < n:
        if types[i] in _NEUTRAL:
            j = i
            while j < n and types[j] in _NEUTRAL:
                j += 1
            left = sos if i == 0 else ("R" if types[i - 1] in ("R", "EN", "AN") else "L")
            right = sos if j == n else ("R" if types[j] in ("R", "EN", "AN") else "L")
            types[i:j] = [left if left == right else sos] * (j - i)
            i = j
        else:
            i += 1
    # I1/I2: resolved levels.
    if rtl:
        return [base + 1 if t in ("L", "EN", "AN") else base for t in types]
    return [base + 1 if t == "R" else base + 2 if t in ("EN", "AN") else base for t in types]


def _visual_order(levels: list[int]) -> list[int]:
    """Logical indexes in left-to-right display order (UAX #9 L2)."""
    order = list(range(len(levels)))
    odd = [level for level in levels if level % 2]
    if not odd:
        return order
    for level in range(max(levels), min(odd) - 1, -1):
        i = 0
        while i < len(order):
            if levels[order[i]] >= level:
                j = i
                while j < len(order) and levels[order[j]] >= level:
                    j += 1
                order[i:j] = order[i:j][::-1]
                i = j
            else:
                i += 1
    return order


def _is_rtl_letter(ch: str) -> bool:
    return _kind(ch) in ("R", "AL", "NSM")


def kitty_order(text: str, rtl: bool) -> list[int]:
    """Logical indexes in the order to send them to kitty: visual order, but each run of RTL
    letters (a word) kept in logical order, since kitty draws a word's letters right to left."""
    levels = _levels(text, rtl)
    order = _visual_order(levels)
    out: list[int] = []
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and _is_rtl_letter(text[order[j]]) and levels[order[j]] % 2:
            j += 1
        if j > i:
            out.extend(reversed(order[i:j]))
            i = j
        else:
            out.append(order[i])
            i += 1
    return out


def reorder_text(text: str, rtl: bool) -> str:
    """`text` as kitty should receive it (see kitty_order); brackets inside RTL runs mirrored."""
    levels = _levels(text, rtl)
    return "".join(_MIRROR.get(text[i], text[i]) if levels[i] % 2 else text[i] for i in kitty_order(text, rtl))


def reorder_strip(strip: Strip, rtl: bool) -> Strip:
    """Reorder one rendered line, keeping each character's style and the line's leading and
    trailing spaces (alignment and padding) in place."""
    cells = [(ch, seg.style) for seg in strip if not seg.control for ch in seg.text]
    text = "".join(ch for ch, _ in cells)
    if not has_rtl(text):
        return strip
    start = len(text) - len(text.lstrip(" "))
    end = len(text.rstrip(" "))
    body = text[start:end]
    levels = _levels(body, rtl)
    out = cells[:start]
    for i in kitty_order(body, rtl):
        ch, style = cells[start + i]
        out.append((_MIRROR.get(ch, ch) if levels[i] % 2 else ch, style))
    out += cells[end:]
    segments: list[Segment] = []
    for ch, style in out:
        if segments and segments[-1].style == style:
            segments[-1] = Segment(segments[-1].text + ch, style)
        else:
            segments.append(Segment(ch, style))
    return Strip(segments, strip.cell_length)


class BidiMixin:
    """Mix into a Textual widget, before the widget class, to reorder its RTL lines for kitty."""

    def bidi_text(self) -> str:
        """The widget's whole text, for the paragraph direction."""
        content = getattr(self, "content", "")
        return content if isinstance(content, str) else getattr(content, "plain", str(content))

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)  # type: ignore[misc]
        if not has_rtl(strip.text):
            return strip
        return reorder_strip(strip, is_rtl(self.bidi_text()))


class _RtlAware(BidiMixin):
    """Mixed into Markdown blocks: reorders RTL lines and tags the block `rtl` (right-aligned)."""

    def set_content(self, content) -> None:
        super().set_content(content)  # type: ignore[misc]
        self.set_class(is_rtl(getattr(content, "plain", str(content))), "rtl")  # type: ignore[attr-defined]


class _BidiTableCell(BidiMixin, MarkdownTableCellContents):
    pass


class _BidiTableContent(MarkdownTableContent):
    def compose(self) -> ComposeResult:
        for widget in super().compose():
            if isinstance(widget, MarkdownTableCellContents):
                widget.__class__ = _BidiTableCell  # same layout, only render_line differs
            yield widget


class _BidiTable(MarkdownTable):
    def compose(self) -> ComposeResult:
        for widget in super().compose():
            if isinstance(widget, MarkdownTableContent):
                widget.__class__ = _BidiTableContent
            yield widget


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
    """Markdown that shows RTL text in order, right-aligns RTL blocks, keeps Notion's single
    line breaks and colours links."""

    DEFAULT_CSS = """
    BidiMarkdown .rtl { text-align: right; }
    BidiMarkdown MarkdownBlock { link-color: $text-accent; link-style: underline; link-color-hover: $accent; }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("parser_factory", _parser)
        super().__init__(*args, **kwargs)

    def get_block_class(self, block_name: str) -> type[MarkdownBlock]:
        cls = super().get_block_class(block_name)
        if cls is MarkdownTable:
            return _BidiTable
        if cls not in _rtl_blocks:
            _rtl_blocks[cls] = type(cls.__name__, (_RtlAware, cls), {})
        return _rtl_blocks[cls]
