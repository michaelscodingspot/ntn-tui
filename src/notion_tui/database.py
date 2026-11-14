"""Database preview helpers: the rows table, view columns and labels, sort labels, and the
request / loaded-rows records the app keeps per database. Pure, no I/O and no Textual."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.cells import cell_len, set_cell_size

from . import ntn
from .ntn import View

# Columns a view may show that the preview can't render as text.
_UNRENDERED_TYPES = {"created_by", "last_edited_by", "relation", "rollup", "files", "button"}
# System columns that only add width to a database preview.
_HIDDEN_COLUMN_TYPES = _UNRENDERED_TYPES | {"created_time", "last_edited_time", "unique_id"}
# View types with no rows to show. Views without a layout of their own (board, calendar, …) are shown as a table.
SKIPPED_VIEW_TYPES = {"form", "chart", "dashboard"}
# View types whose rows can be edited in the grid. Other views stay read-only.
EDITABLE_VIEW_TYPES = {"table", "list"}
# How View mode can lay out a view's rows; a view of one of these types opens in its own.
LAYOUTS = ("table", "list")


def trash_question(title: str, data_sources: int) -> str:
    """The confirmation for trashing a database; says so when more than the listed table goes with it."""
    if data_sources > 1:
        return f"Move database “{title}”, with all {data_sources} of its data sources and their rows, to the Notion trash?"
    return f"Move database “{title}” and all its rows to the Notion trash?"


def rows_markdown(rows: list[dict], has_more: bool, columns: list[str] | None = None, layout: str = "table") -> str:
    """A database's rows as a Markdown table, or a bullet list with `layout` "list".

    Without `columns`: the title column first, then non-empty columns. With `columns` (a view's
    visible columns, in its order): those columns, minus ones that can't be shown as text.
    """
    if not rows:
        return "_(no rows)_"
    types = {name: prop.get("type") for name, prop in rows[0].get("properties", {}).items()}
    if columns is None:
        columns = []
        for row in rows:
            for name, prop in row.get("properties", {}).items():
                if name not in columns and prop.get("type") not in _HIDDEN_COLUMN_TYPES:
                    columns.append(name)
        columns.sort(key=lambda name: rows[0]["properties"].get(name, {}).get("type") != "title")
        drop_empty = True
    else:
        columns = [c for c in columns if c in types and types[c] not in _UNRENDERED_TYPES]
        drop_empty = False
    if not columns:
        return "_(no columns to show)_"
    cells = [[ntn.value_text(row["properties"][c]).replace("\n", " ") if c in row["properties"] else "" for c in columns] for row in rows]
    keep = [i for i, c in enumerate(columns) if i == 0 or not drop_empty or any(r[i] for r in cells)]

    if layout == "list":
        title = next((i for i in keep if types.get(columns[i]) == "title"), keep[0])
        out = [_list_item(r[title], [r[i] for i in keep if i != title]) for r in cells]
    else:
        out = [_table_line([columns[i].strip() for i in keep]), "|" + "---|" * len(keep)]
        out += [_table_line([r[i] for i in keep]) for r in cells]
    if has_more:
        out.append(f"\n_{len(rows)} rows shown. Press m for more._")
    return "\n".join(out)


def _table_line(values: list[str]) -> str:
    return "| " + " | ".join(v.replace("|", "\\|") for v in values) + " |"


def _list_item(title: str, values: list[str]) -> str:
    """One row of the list layout: the title, then its non-empty values, as Notion's list shows them."""
    return "- " + " · ".join([f"**{title or 'Untitled'}**", *(v for v in values if v)])


def view_columns(view: View | None, schema: list[dict] | None) -> list[str] | None:
    """Names of a view's visible columns in its order, title included; None = no column setup."""
    if view is None or not view.columns or not schema:
        return None
    by_id = {p["id"]: p for p in schema}
    names = [by_id[c["property_id"]]["name"] for c in view.columns if c.get("visible") and c.get("property_id") in by_id]
    title = next((p["name"] for p in schema if p["type"] == "title"), None)
    if title and title not in names:
        names.insert(0, title)  # list and board views keep the title out of their column list
    return names


def can_edit_rows(view: View | None) -> bool:
    """Whether a view's rows can be edited in the grid; a database with no views shows a plain table."""
    return view is None or view.type in EDITABLE_VIEW_TYPES


def can_switch_layout(view: View | None) -> bool:
    """Whether a view's rows can change layout; a database with no views shows a plain table."""
    return view is not None and view.type in LAYOUTS


def next_layout(layout: str) -> str:
    return LAYOUTS[(LAYOUTS.index(layout) + 1) % len(LAYOUTS)]


def grid_columns(schema: list[dict], columns: list[str] | None) -> list[str]:
    """The grid's columns: a view's columns, or every property the plain table can show, even when empty."""
    types = {p["name"]: p["type"] for p in schema}
    if columns is None:
        return [p["name"] for p in schema if p["type"] not in _HIDDEN_COLUMN_TYPES]
    return [c for c in columns if c in types and types[c] not in _UNRENDERED_TYPES]


MIN_COLUMN_WIDTH = 8  # narrowest a cut grid column gets; past that the grid scrolls sideways


def column_widths(natural: list[int], room: int) -> list[int]:
    """Widths for grid columns sharing `room` cells: a column narrower than its fair share keeps its
    width, and what it leaves goes to the wider ones, so one long column can fill the space."""
    widths = list(natural)
    left, count = room, len(natural)
    for i in sorted(range(len(natural)), key=natural.__getitem__):
        widths[i] = min(natural[i], max(left // count, MIN_COLUMN_WIDTH))
        left, count = left - widths[i], count - 1
    return widths


WRAPPED_ROW_LINES = 10  # most lines the expanded row (`z`) shows
MORE_LINES = "⏬"  # ends a grid cell that has more lines than it shows; a long line is cut with "…"


def natural_width(text: str, expand: bool) -> int:
    """Cells a grid cell's text wants: its first line, and the more-lines mark when there are others;
    with `expand` (`z`) also its longest line, so widths don't change as the expanded row moves."""
    lines = text.split("\n")
    one_line = cell_len(lines[0]) + (1 + cell_len(MORE_LINES) if len(lines) > 1 else 0)
    return max(one_line, *map(cell_len, lines)) if expand else one_line


def with_more_mark(line: str, width: int | None) -> str:
    """A line ending in the more-lines mark: at the column's right edge, the line cut to make room."""
    if width is None:
        return f"{line} {MORE_LINES}"
    return set_cell_size(line, max(width - cell_len(MORE_LINES), 0)) + MORE_LINES


def wrap_lines(text: str, width: int, most: int = WRAPPED_ROW_LINES) -> list[str]:
    """A cell's text as lines of at most `width` cells: Notion's line breaks kept, words wrapped,
    a word longer than the width broken, a line's indent kept on its first row. Past `most` lines
    the last kept one ends with the more-lines mark."""
    width = max(width, 1)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        body = paragraph.lstrip(" ")
        line = paragraph[:len(paragraph) - len(body)][:width - 1]  # the indent, e.g. a nested list item
        for word in body.split(" "):
            joined = f"{line} {word}" if line.strip() else line + word
            if cell_len(joined) <= width:
                line = joined
                continue
            if line.strip():
                lines.append(line)
            line = word
            while cell_len(line) > width:
                head = set_cell_size(line, width).rstrip(" ") or line[0]  # a wide letter isn't split
                lines.append(head)
                line = line[len(head):]
        lines.append(line)
    if len(lines) > most:
        lines = lines[:most - 1] + [with_more_mark(lines[most - 1], width)]
    return lines


def find_row(rows: list[dict], row_id: str | None) -> dict | None:
    return next((row for row in rows if row["id"] == row_id), None)


def insert_next_to(rows: list[dict], row: dict, anchor_id: str, after: bool) -> None:
    """Put a row right after / before the row with ID anchor_id, or at the end when that row is gone."""
    index = next((i for i, r in enumerate(rows) if r["id"] == anchor_id), None)
    rows.insert(len(rows) if index is None else index + after, row)


def replace_row(rows: list[dict], fresh: dict) -> None:
    """Swap in the row as Notion returned it after a change."""
    for i, row in enumerate(rows):
        if row["id"] == fresh["id"]:
            rows[i] = fresh
            return


def sort_level_name(level: dict, schema: list[dict] | None) -> str:
    """The property (or timestamp) one sort level sorts by."""
    if "property" not in level:
        return level.get("timestamp", "?").replace("_", " ")
    return next((p["name"].strip() for p in schema or [] if p["id"] == level["property"]), level["property"])


def sort_label(sorts: list | None, schema: list[dict] | None) -> str:
    """ "Priority ↑, Due date ↓" for a sorts list."""
    return ", ".join(
        f"{sort_level_name(level, schema)} {'↓' if level.get('direction') == 'descending' else '↑'}" for level in sorts or []
    )


def view_label(view: View) -> str:
    name = view.name.strip() or "untitled"
    return name if view.type in LAYOUTS else f"{name} ({view.type})"


@dataclass
class DbRequest:
    """What a database preview asks Notion for: the current view plus the query line and sort override."""

    views: list[View] | None  # None = not fetched yet
    schema: list[dict] | None
    view: View | None
    query_text: str
    sorts_override: list | None
    filter: dict | None = None
    sorts: list | None = None
    columns: list[str] | None = None
    layout: str = "table"
    custom_layout: bool = False  # `layout` was picked with `t` and differs from the view's own
    error: str = ""  # the saved query line no longer parses


@dataclass
class DbRows:
    """Rows loaded for the database preview, for paging and the row count."""

    page_id: str
    rows: list[dict] = field(default_factory=list)
    cursor: str | None = None
    columns: list[str] | None = None
    filter: dict | None = None  # what page 1 was queried with; later pages must ask the same
    sorts: list | None = None
    reloading: bool = False  # a new query is on its way: these rows are about to be replaced
    failed: bool = False  # Notion refused the query: no rows, only the error


def rows_key(rows: list[dict]) -> list[tuple]:
    """What a change to rows shows in: order, edit time and values (two edits in one minute share an edit time)."""
    return [(r.get("id"), r.get("last_edited_time"), r.get("properties")) for r in rows]


def people_names(rows: list[dict]) -> dict[str, str]:
    """Person name → user ID, from people properties of loaded rows (the query line filters people by name)."""
    found: dict[str, str] = {}
    for row in rows:
        for prop in row.get("properties", {}).values():
            if prop.get("type") == "people":
                for person in prop.get("people") or []:
                    if person.get("name") and person.get("id"):
                        found[person["name"]] = person["id"]
    return found
