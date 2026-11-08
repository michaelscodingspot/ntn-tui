"""The database grid of Edit mode: rows and columns with a cell cursor (a Textual DataTable)."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.geometry import Offset, Region
from textual.widgets import DataTable

from .bidi import is_rtl, reorder_text
from .rows import cell_text

NEW_ROW = "new-row"  # key of the row shown while its title is typed and sent


def _cell(text: str) -> Text:
    return Text(reorder_text(text, is_rtl(text)), no_wrap=True)


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
        Binding("x", "app.row_trash", "delete row"),
        Binding("u", "app.row_restore", "undo delete"),
        Binding("e", "app.row_body", "row body"),
        Binding("y,ctrl+c", "app.row_copy", "copy cell"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(cursor_type="cell", **kwargs)
        self.names: list[str] = []  # the property behind each column
        self.source: list[dict] | None = None  # the rows list this grid was drawn from
        self.count = 0  # real rows drawn

    def stale(self, rows: list[dict], names: list[str]) -> bool:
        return rows is not self.source or len(rows) != self.count or names != self.names

    def show(self, rows: list[dict], names: list[str]) -> None:
        """Draw the rows, keeping the cursor on the same row and column when they are still there."""
        row_id, at = self.current_row_id, self.cursor_coordinate
        self.clear(columns=True)
        self.names, self.source, self.count = names, rows, len(rows)
        for name in names:
            self.add_column(name.strip(), key=name)
        for row in rows:
            self.add_row(*self._cells(row), key=row["id"], label=" ")
        index = next((i for i, row in enumerate(rows) if row["id"] == row_id), min(at.row, len(rows) - 1))
        self.move_cursor(row=max(index, 0), column=min(at.column, max(len(names) - 1, 0)), animate=False)

    def _cells(self, row: dict) -> list[Text]:
        props = row.get("properties", {})
        return [_cell(cell_text(props[name]) if name in props else "") for name in self.names]

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
        if self.has_row(row_id):
            self.update_cell(row_id, name, _cell(text), update_width=True)

    def redraw_row(self, row: dict) -> None:
        if self.has_row(row["id"]):
            for name, cell in zip(self.names, self._cells(row)):
                self.update_cell(row["id"], name, cell, update_width=True)

    def mark(self, row_id: str, mark: str) -> None:
        """Show one character in the row's gutter: … saving, ✓ saved, space nothing."""
        if self.has_row(row_id):
            self.rows[row_id].label = Text(mark)
            self._clear_caches()
            self.refresh()

    def saving(self, row_id: str) -> bool:
        return self.has_row(row_id) and str(self.rows[row_id].label) == "…"

    def drop_row(self, row_id: str) -> None:
        """Remove a row the app already took out of its rows list."""
        if self.has_row(row_id):
            self.remove_row(row_id)
            if row_id != NEW_ROW:
                self.count -= 1

    def select_row(self, row_id: str) -> None:
        if self.has_row(row_id):
            self.move_cursor(row=self.get_row_index(row_id), animate=False)

    def add_new_row(self, name: str) -> None:
        """Add the empty row a new title is typed into and put the cursor on its title cell."""
        self.add_row(*["" for _ in self.names], key=NEW_ROW, label=" ")
        self.move_cursor(row=self.row_count - 1, column=self.names.index(name), animate=False)

    def cell_region(self) -> Region:
        """Where the cursor's cell is on the screen, for placing its editor."""
        cell = self._get_cell_region(Coordinate(*self.cursor_coordinate))
        return cell.translate(self.region.offset - Offset(int(self.scroll_x), int(self.scroll_y)))
