"""Right-to-left text (Hebrew, Arabic) for terminals that draw cells strictly left to right.

Most terminals (kitty included) do no bidi reordering, so RTL text shows backwards. Here each
rendered line is put into visual order after Textual has wrapped it, keeping each character's
style. The paragraph direction comes from the widget's whole text (first strong character),
so a wrapped continuation line keeps the direction of its paragraph.

This is a compact Unicode Bidirectional Algorithm (UAX #9) for one embedding level: no explicit
embeddings or isolates, which Notion text practically never contains.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from rich.segment import Segment
from textual.app import ComposeResult
from textual.strip import Strip
from textual.widgets import Markdown
from textual.widgets._markdown import MarkdownBlock, MarkdownTable, MarkdownTableCellContents, MarkdownTableContent

_RTL_CHARS = re.compile("[֐-ࣿיִ-﷿ﹰ-ﻼ\U00010800-\U00010fff\U0001e800-\U0001efff]")
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
            types[i] = "ON" if prev in ("LRI", "RLI", "FSI", "PDI") else prev
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
        levels = [base + 1 if t in ("L", "EN", "AN") else base for t in types]
    else:
        levels = [base + 1 if t == "R" else base + 2 if t in ("EN", "AN") else base for t in types]
    # L1: trailing whitespace goes back to the paragraph level.
    for i in range(n - 1, -1, -1):
        if _kind(text[i]) not in ("WS", "S", "BN", "B"):
            break
        levels[i] = base
    return levels


def _visual_order(levels: list[int]) -> list[int]:
    """Logical indexes in left-to-right display order (UAX #9 L2)."""
    order = list(range(len(levels)))
    odd = [level for level in levels if level % 2]
    if not odd:
        return order
    lowest_odd = min(odd)
    for level in range(max(levels), lowest_odd - 1, -1):
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


def reorder_strip(strip: Strip, rtl: bool, width: int | None = None, head: int = 0) -> Strip:
    """Put one rendered line into visual order, carrying each character's style.

    `width`: right-align an RTL line within that many cells (preview paragraphs). Without it the
    text stays where it was. `head`: leading cells kept in place (e.g. a list marker).
    """
    cells = [(ch, seg.style) for seg in strip if not seg.control for ch in seg.text]
    text = "".join(ch for ch, _ in cells)
    if not has_rtl(text):
        return strip
    end = len(text.rstrip(" "))
    prefix, body, tail, text = cells[:head], cells[head:end], cells[end:], text[head:end]
    levels = _levels(text, rtl)
    out = []
    for i in _visual_order(levels):
        ch, style = body[i]
        if levels[i] % 2:
            ch = _MIRROR.get(ch, ch)
        out.append((ch, style))
    if width is not None and rtl:
        pad_style = tail[0][1] if tail else None
        out = [(" ", pad_style)] * max(0, width - len(prefix) - len(out) - len(tail)) + out
        out = prefix + tail + out
    else:
        out = prefix + out + tail
    segments: list[Segment] = []
    for ch, style in out:
        if segments and segments[-1].style == style:
            segments[-1] = Segment(segments[-1].text + ch, style)
        else:
            segments.append(Segment(ch, style))
    return Strip(segments)


class BidiMixin:
    """Mix into a Textual widget, before the widget class, to show its RTL text in visual order."""

    BIDI_ALIGN = True
    """Right-align RTL paragraphs (preview). False keeps the text where it is (lists, one-liners)."""
    bidi_head = 0
    """Leading cells left in place, such as a list marker."""

    def bidi_text(self) -> str:
        """The widget's whole text, for the paragraph direction."""
        content = getattr(self, "content", "")
        return content if isinstance(content, str) else getattr(content, "plain", "")

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)  # type: ignore[misc]
        if not has_rtl(strip.text):
            return strip
        width = max(strip.cell_length, self.content_size.width) if self.BIDI_ALIGN else None  # type: ignore[attr-defined]
        return reorder_strip(strip, is_rtl(self.bidi_text()), width, self.bidi_head)


class _BidiTableCell(BidiMixin, MarkdownTableCellContents):
    pass


class _BidiTableContent(MarkdownTableContent):
    def compose(self) -> ComposeResult:
        for widget in super().compose():
            if isinstance(widget, MarkdownTableCellContents):
                widget.__class__ = _BidiTableCell  # same layout, only render_line differs
            yield widget


class _BidiTable(BidiMixin, MarkdownTable):
    def compose(self) -> ComposeResult:
        for widget in super().compose():
            if isinstance(widget, MarkdownTableContent):
                widget.__class__ = _BidiTableContent
            yield widget


_bidi_blocks: dict[type, type] = {}


class BidiMarkdown(Markdown):
    """Markdown whose blocks (paragraphs, headings, list items, table cells) show RTL text correctly."""

    def get_block_class(self, block_name: str) -> type[MarkdownBlock]:
        cls = super().get_block_class(block_name)
        if cls is MarkdownTable:
            return _BidiTable
        if cls not in _bidi_blocks:
            _bidi_blocks[cls] = type(cls.__name__, (BidiMixin, cls), {})
        return _bidi_blocks[cls]
