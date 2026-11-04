from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip

from notion_tui.bidi import is_rtl, reorder_strip, reorder_text


def test_rtl_words_swap_letters_stay():
    assert reorder_text("רשת אקווריום", True) == "אקווריום רשת"
    assert reorder_text("צבע לשכן לקבוע לשבוע הבא", True) == "הבא לשבוע לקבוע לשכן צבע"


def test_single_rtl_word_in_ltr_text_unchanged():
    assert reorder_text("Ilana אילנה", False) == "Ilana אילנה"
    assert reorder_text("aerc: no hebrew", False) == "aerc: no hebrew"


def test_ltr_words_inside_rtl_paragraph_keep_their_order():
    assert reorder_text("קניתי Raspberry Pi היום", True) == "היום Raspberry Pi קניתי"


def test_numbers_and_brackets():
    assert reorder_text("קניתי 3 דברים (זול)", True) == "(זול) דברים 3 קניתי"


def test_paragraph_direction():
    assert is_rtl("רשת Yael")
    assert not is_rtl("Yael רשת")
    assert not is_rtl("123")


def test_strip_keeps_padding_and_styles():
    bold = Style(bold=True)
    strip = Strip([Segment("   "), Segment("רשת", bold), Segment(" אקווריום "), Segment("  ")])
    out = reorder_strip(strip, True)
    assert out.text == "   אקווריום רשת   "
    assert out.cell_length == strip.cell_length
    assert [seg.style for seg in out if seg.text == "רשת"] == [bold]
