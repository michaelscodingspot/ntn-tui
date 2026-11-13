"""The database grid of Edit mode: rows and columns with a cell cursor (a Textual DataTable)."""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.text import Text
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.geometry import Offset, Region
from textual.widgets import DataTable

from .bidi import is_rtl, reorder_text
from .database import MORE_LINES, column_widths, natural_width, with_more_mark, wrap_lines
from .rows import cell_text

NEW_ROW = "new-row"  # key of the row shown while its title is typed and sent
SAVED_MARK_SECONDS = 1.5  # how long a row shows ✓ after a save


def _cell(text: str, width: int | None, wrap: bool) -> Text:
    """A cell on one line, or wrapped to the column's width. A long line is cut with "…"; a cell
    with more lines than it shows ends with the more-lines mark (one line: its first line only)."""
    rtl = is_rtl(text)

    def ordered(line: str) -> str:  # RTL words in visual order, the mark kept at the end
        body, mark = (line[:-len(MORE_LINES)], MORE_LINES) if line.endswith(MORE_LINES) else (line, "")
        return reorder_text(body, rtl) + mark

    if wrap and width is not None:
        return Text("\n".join(ordered(line) for line in wrap_lines(text, width)), no_wrap=True)
    first, more = text.split("\n", 1)[0], "\n" in text
    if more:
        text = with_more_mark(first, width)
    elif width is not None and cell_len(text) > width:
        text = set_cell_size(text, width - 1) + "…"  # cut the logical text, then order RTL words
    return Text(ordered(text), no_wrap=True)


class RowGrid(DataTable):
    BINDINGS = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("h", "cursor_left", "left", show=False),
        Binding("l", "cursor_right", "right", show=False),
        Binding("g", "scroll_top", "first row", show=False),
        Binding("G", "scroll_bottom", "last row", show=False),
        Binding("enter", "select_cursor", "edit cell"),
        Binding("space", "select_cursor", "edit cell", show=False),
        Binding("n", "app.row_new", "new row"),
        Binding("i", "app.row_insert(True)", "insert row below"),
        Binding("I", "app.row_insert(False)", "insert row above"),
        Binding("x", "app.row_trash", "delete row"),
        Binding("u", "app.row_restore", "undo delete"),
        Binding("alt+k,alt+up", "app.row_move(-1)", "move row up", key_display="alt+↑"),
        Binding("alt+j,alt+down", "app.row_move(1)", "move row down", key_display="alt+↓"),
        Binding("e", "app.row_body", "row body"),
        Binding("E", "app.cell_in_editor", "cell in $EDITOR"),
        Binding("z", "app.db_expand", "expand row"),
        Binding("ctrl+o", "app.row_claude", "Claude task"),
        Binding("y,ctrl+c", "app.row_copy", "copy cell"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(cursor_type="cell", **kwargs)
        self.names: list[str] = []  # the property behind each column
        self.heads: list[str] = []  # each column's header text
        self.source: list[dict] | None = None  # the rows list this grid was drawn from
        self.texts: dict[str, list[str]] = {}  # each row's cells in full, by row key, in row order
        self.widths: list[int | None] = []  # longest text each column shows; None: not laid out yet
        self.expand = False  # the cursor's row grows to show its cells wrapped (`z`); others stay one line
        self.expanded: str | None = None  # the row drawn wrapped now

    @property
    def count(self) -> int:
        """Real rows drawn, not counting the row a new title is typed into."""
        return len(self.texts) - (NEW_ROW in self.texts)

    def stale(self, rows: list[dict], names: list[str]) -> bool:
        return rows is not self.source or len(rows) != self.count or names != self.names

    def show(self, rows: list[dict], names: list[str], expand: bool) -> None:
        """Draw the rows, keeping the cursor on the same row and column when they are still there;
        with `expand` the cursor's row wrapped."""
        self.names, self.heads, self.source = names, [name.strip() for name in names], rows
        self.expand = expand
        self.texts = {row["id"]: self._texts(row) for row in rows}
        self._fit()
        self._draw()

    def _draw(self) -> None:
        """Build the table from the kept texts, keeping the cursor and the row marks.
        Rebuilt rather than updated cell by cell: Textual re-measures a whole column for each narrower cell."""
        row_id, at = self.current_row_id, self.cursor_coordinate
        marks = {key: row.label for key, row in self.rows.items()}
        self.clear(columns=True)
        self.expanded = None  # rows are added one line each; the cursor's row grows once it is placed
        for name, head in zip(self.names, self.heads):
            self.add_column(head, key=name)
        for key in self.texts:
            self.add_row(*self._cells(key), key=key, label=marks.get(key, " "))
        keys = list(self.texts)
        index = keys.index(row_id) if row_id in self.texts else min(at.row, len(keys) - 1)
        self.move_cursor(row=max(index, 0), column=min(at.column, max(len(self.names) - 1, 0)), animate=False)
        self._expand_cursor_row()

    def _texts(self, row: dict) -> list[str]:
        props = row.get("properties", {})
        return [cell_text(props[name]) if name in props else "" for name in self.names]

    def _cells(self, row_id: str, wrap: bool = False) -> list[Text]:
        return [_cell(text, width, wrap) for text, width in zip(self.texts[row_id], self.widths)]

    def _redraw_cells(self, row_id: str, wrap: bool) -> None:
        """Draw one row's cells again, one line or wrapped, with the row as tall as its tallest cell."""
        cells = self._cells(row_id, wrap)
        for name, cell in zip(self.names, cells):
            self.update_cell(row_id, name, cell)
        self.rows[row_id].height = max((cell.plain.count("\n") + 1 for cell in cells), default=1)
        self._clear_caches()
        self._require_update_dimensions = True  # the table's height changes with the row's

    def _expand_cursor_row(self) -> None:
        """With `z` on, wrap the cursor's row and put the row it left back on one line."""
        target = self.current_row_id if self.expand else None
        if target == self.expanded:
            return
        if self.expanded is not None and self.has_row(self.expanded):
            self._redraw_cells(self.expanded, False)
        self.expanded = target
        if target is not None:
            self._redraw_cells(target, True)
            self.call_after_refresh(self._scroll_cursor_into_view)

    def watch_cursor_coordinate(self, old: Coordinate, new: Coordinate) -> None:
        super().watch_cursor_coordinate(old, new)
        self._expand_cursor_row()

    def _fit(self) -> bool:
        """Share the grid's width between its columns, so text is cut only where it doesn't fit.
        True when the widths changed."""
        old = self.widths
        if not self.size.width:  # not laid out yet: sized on the first resize
            self.widths = [None] * len(self.names)
        else:
            natural = [max(natural_width(text, self.expand) for text in column)
                       for column in zip(self.heads, *self.texts.values())]
            gutter = 1 + 2 * self.cell_padding  # the column of row marks
            room = self.scrollable_content_region.width - gutter - 2 * self.cell_padding * len(self.heads)
            self.widths = column_widths(natural, room)
        return self.widths != old

    def _refit(self) -> bool:
        """Size the columns again, rebuilding the table when their widths changed."""
        if self._fit():
            self._draw()
            return True
        return False

    def _changed(self, row_id: str) -> None:
        """Draw a row whose texts changed, or the whole table when that moved the column widths."""
        if not self._refit():  # the widths stayed: every cell already fits its column
            self._redraw_cells(row_id, row_id == self.expanded)

    def set_expand(self, on: bool) -> None:
        """Wrap the cursor's row (as tall as its longest cell, following the cursor) or keep every row on one line."""
        if on != self.expand:
            self.expand = on
            if self.texts:
                self._fit()
                self._draw()

    def on_resize(self) -> None:
        if self.texts:
            self._refit()

    @property
    def current_row_id(self) -> str | None:
        if not self.is_valid_coordinate(self.cursor_coordinate):
            return None
        return self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value

    @property
    def current_name(self) -> str | None:
        return self.names[self.cursor_column] if self.cursor_column < len(self.names) else None

    def has_row(self, row_id: str) -> bool:
        return row_id in self.rows

    def set_cell(self, row_id: str, name: str, text: str) -> None:
        if self.has_row(row_id) and name in self.names:
            self.texts[row_id][self.names.index(name)] = text
            self._changed(row_id)

    def redraw_row(self, row: dict) -> None:
        if self.has_row(row["id"]):
            self.texts[row["id"]] = self._texts(row)
            self._changed(row["id"])

    def mark(self, row_id: str, mark: str) -> None:
        """Show one character in the row's gutter: … saving, ✓ saved, space nothing."""
        if self.has_row(row_id):
            self.rows[row_id].label = Text(mark)
            self._clear_caches()
            self.refresh()

    def mark_saved(self, row_id: str) -> None:
        """✓ in the row's gutter for a moment."""
        self.mark(row_id, "✓")
        self.set_timer(SAVED_MARK_SECONDS, lambda: self.mark(row_id, " "))

    def swap_rows(self, first: dict, second: dict) -> None:
        """Swap two rows the app swapped in its rows list, with their changed values; the cursor stays on its row."""
        if not (self.has_row(first["id"]) and self.has_row(second["id"])):
            return
        keys = list(self.texts)
        i, j = keys.index(first["id"]), keys.index(second["id"])
        keys[i], keys[j] = keys[j], keys[i]
        self.texts = {key: self.texts[key] for key in keys}
        for row in (first, second):
            self.texts[row["id"]] = self._texts(row)
        self._fit()
        self._draw()

    def saving(self, row_id: str) -> bool:
        return self.has_row(row_id) and str(self.rows[row_id].label) == "…"

    def drop_row(self, row_id: str) -> None:
        """Remove a row the app already took out of its rows list."""
        if self.has_row(row_id):
            self.remove_row(row_id)
            del self.texts[row_id]
            self._refit()

    def select_row(self, row_id: str) -> None:
        if self.has_row(row_id):
            self.move_cursor(row=self.get_row_index(row_id), animate=False)

    def add_new_row(self, name: str, index: int | None = None) -> None:
        """Add the empty row a new title is typed into, at the bottom or at a row index,
        and put the cursor on its title cell."""
        blank = ["" for _ in self.names]
        if index is None or index >= len(self.texts):
            self.texts[NEW_ROW] = blank
            self.add_row(*self._cells(NEW_ROW), key=NEW_ROW, label=" ")
        else:  # a DataTable only appends: rebuild with the new row in its place
            items = list(self.texts.items())
            items.insert(index, (NEW_ROW, blank))
            self.texts = dict(items)
            self._draw()
        self.move_cursor(row=self.get_row_index(NEW_ROW), column=self.names.index(name), animate=False)

    def cell_region(self) -> Region:
        """Where the cursor's cell is on the screen, for placing its editor."""
        cell = self._get_cell_region(Coordinate(*self.cursor_coordinate))
        return cell.translate(self.region.offset - Offset(int(self.scroll_x), int(self.scroll_y)))
