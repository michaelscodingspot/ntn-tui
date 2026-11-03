"""Database preview helpers: the rows table, view columns and labels, sort labels, and the
request / loaded-rows records the app keeps per database. Pure, no I/O and no Textual."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ntn
from .ntn import View

# Columns a view may show that the preview can't render as text.
_UNRENDERED_TYPES = {"created_by", "last_edited_by", "relation", "rollup", "files", "button"}
# System columns that only add width to a database preview.
_HIDDEN_COLUMN_TYPES = _UNRENDERED_TYPES | {"created_time", "last_edited_time", "unique_id"}
# View types with no rows to show. Other non-table views (board, calendar, …) are shown as a table.
SKIPPED_VIEW_TYPES = {"form", "chart", "dashboard"}


def rows_markdown(rows: list[dict], has_more: bool, columns: list[str] | None = None) -> str:
    """A database's rows as a Markdown table.

    Without `columns`: the title column first, then non-empty columns. With `columns` (a view's
    visible columns, in its order): those columns, minus ones that can't be shown as text.
    """
    if not rows:
        return "_(no rows)_"
    if columns is None:
        columns = []
        for row in rows:
            for name, prop in row.get("properties", {}).items():
                if name not in columns and prop.get("type") not in _HIDDEN_COLUMN_TYPES:
                    columns.append(name)
        columns.sort(key=lambda name: rows[0]["properties"].get(name, {}).get("type") != "title")
        drop_empty = True
    else:
        types = {name: prop.get("type") for row in rows[:1] for name, prop in row.get("properties", {}).items()}
        columns = [c for c in columns if c in types and types[c] not in _UNRENDERED_TYPES]
        drop_empty = False
    if not columns:
        return "_(no columns to show)_"
    cells = [[ntn.value_text(row["properties"][c]) if c in row["properties"] else "" for c in columns] for row in rows]
    keep = [i for i, c in enumerate(columns) if i == 0 or not drop_empty or any(r[i] for r in cells)]

    def line(values: list[str]) -> str:
        return "| " + " | ".join(v.replace("|", "\\|").replace("\n", " ") for v in values) + " |"

    out = [line([columns[i].strip() for i in keep]), "|" + "---|" * len(keep)]
    out += [line([r[i] for i in keep]) for r in cells]
    if has_more:
        out.append(f"\n_{len(rows)} rows shown. Press m for more._")
    return "\n".join(out)


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
    return name if view.type in ("table", "list") else f"{name} ({view.type})"


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
