"""Editing a database row's properties: which types can be edited and how, and the conversion
between what the user sees and Notion's property values. Pure, no I/O and no Textual."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import Enum

from .ntn import value_text

_TEXT_LIMIT = 2000  # Notion's limit for one piece of rich text

_EDITORS = {
    "title": "text",
    "rich_text": "text",
    "url": "text",
    "email": "text",
    "phone_number": "text",
    "number": "text",
    "date": "text",
    "select": "pick",
    "status": "pick",
    "multi_select": "multi",
    "people": "multi",
    "checkbox": "check",
}
_NEW_OPTION_TYPES = {"select", "multi_select"}  # Notion can't add a status option through a row


def editor(kind: str) -> str | None:
    """How a property type is edited: text | pick | multi | check; None = read-only."""
    return _EDITORS.get(kind)


def can_add_options(kind: str) -> bool:
    return kind in _NEW_OPTION_TYPES


def multi_line(kind: str) -> bool:
    """Whether a property type keeps line breaks: text does, a title is one line in Notion."""
    return kind == "rich_text"


def cell_text(prop: dict) -> str:
    """A property's text for a grid cell, line breaks kept where the type has them; the grid puts
    it on one line or wraps it."""
    text = value_text(prop)
    return text if multi_line(prop.get("type", "")) else _one_line(text)


def _one_line(text: str) -> str:
    return text.replace("\n", " ")  # cutting it to a width is the caller's job


def edit_text(prop: dict) -> str:
    """A property's value as the text the cell editor starts with."""
    if prop.get("type") != "date":
        return cell_text(prop)
    return copy_text(prop)  # in full, so a time of day isn't lost


def copy_text(prop: dict) -> str:
    """A property's full value for the clipboard: not cut for the cell, line breaks and times of day kept."""
    kind = prop.get("type")
    if kind == "date":
        value = prop.get("date") or {}
        start, end = value.get("start", ""), value.get("end")
        return f"{start} → {end}" if end else start
    if kind in ("created_time", "last_edited_time"):
        return prop.get(kind) or ""
    return value_text(prop)


def _rich_text(text: str) -> list[dict]:
    return [{"text": {"content": text[i:i + _TEXT_LIMIT]}} for i in range(0, len(text), _TEXT_LIMIT)]


def _date_part(text: str) -> str:
    text = text.strip()
    try:
        date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"“{text}” isn't a date. Write it as 2026-11-20.") from None
    return text


def _date_value(text: str) -> dict | None:
    if not text:
        return None
    parts = text.replace("->", "→").split("→")
    if len(parts) > 2:
        raise ValueError("A date has one start and one end: 2026-11-20 → 2026-11-25.")
    value = {"start": _date_part(parts[0])}
    if len(parts) == 2:
        value["end"] = _date_part(parts[1])
    return value


def _number_value(text: str) -> int | float | None:
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        raise ValueError(f"“{text}” isn't a number.") from None
    if not math.isfinite(number):
        raise ValueError(f"“{text}” isn't a number.")
    return _whole_as_int(number)


def parse_text(kind: str, text: str) -> tuple[dict, str]:
    """(Notion property value, text for the cell) for what was typed. Raises ValueError with a message."""
    text = text.strip()
    match kind:
        case "title" | "rich_text":
            text = text if multi_line(kind) else _one_line(text)
            return {kind: _rich_text(text)}, text
        case "number":
            value = {kind: _number_value(text)}
        case "date":
            value = {kind: _date_value(text)}
        case _:  # url, email, phone: empty clears them
            value = {kind: text or None}
    return value, cell_text({"type": kind, **value})


def picked(prop: dict) -> list[str]:
    """Names chosen in a select, status, multi-select or people property."""
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("select", "status"):
        return [value["name"]] if value else []
    return [item.get("name", "") for item in value or []]


def pick_value(kind: str, names: list[str], people: dict[str, str] | None = None) -> tuple[dict, str]:
    """(Notion property value, text for the cell) for names chosen in a pick list. `people` = name → user ID."""
    match kind:
        case "select" | "status":
            value = {"name": names[0]} if names else None
        case "people":
            value = [{"id": (people or {})[name]} for name in names]
        case _:
            value = [{"name": name} for name in names]
    return {kind: value}, ", ".join(names)


def toggled(prop: dict) -> tuple[dict, str]:
    """(Notion property value, text for the cell) for a checkbox flipped."""
    value = not prop.get("checkbox")
    return {"checkbox": value}, "✓" if value else ""


def check_option(name: str) -> str:
    """A new option's name. Raises ValueError with a message: Notion refuses commas in option names."""
    name = name.strip()
    if "," in name:
        raise ValueError("Option names can't contain a comma.")
    return name


ORDER_NAME = "_ord"  # a number column that keeps a database's rows in a hand-made order
ORDER_GAP = 8  # a row added after the last `_ord` leaves this much room for rows inserted between


def order_column(schema: list[dict]) -> str | None:
    """The database's `_ord` number column, if it has one."""
    return next((p["name"] for p in schema if p["name"].strip() == ORDER_NAME and p["type"] == "number"), None)


def _number(row: dict, name: str) -> float | None:
    prop = row["properties"].get(name) or {}
    return prop.get("number") if prop.get("type") == "number" else None


def new_order(rows: list[dict], index: int, after: bool, name: str) -> float | None:
    """The `_ord` (column `name`) for a row put right after / before rows[index]: halfway to its
    neighbour on that side, or one past it when there's no neighbour, the neighbour has no `_ord`,
    or its `_ord` isn't on that side (the rows aren't shown in `_ord` order). None when rows[index]
    has no `_ord` itself."""
    here = _number(rows[index], name)
    if here is None:
        return None
    step = 1 if after else -1
    near = index + step
    there = _number(rows[near], name) if 0 <= near < len(rows) else None
    if there is None or (there - here) * step <= 0:
        return here + step
    return (here + there) / 2


def after_last(highest: float | None) -> float:
    """The `_ord` for a row added after every other: the highest `_ord` plus a gap (the gap alone
    when no row has an `_ord` yet)."""
    return (highest or 0) + ORDER_GAP


class Order(Enum):
    """`_ord` outcomes that aren't a number known up front."""

    LAST = "last"  # past the highest `_ord` in the database, asked from Notion when the row is created
    NEEDS_MORE = "needs more"  # the next row isn't loaded, so there's nothing to go halfway to


def insert_order(rows: list[dict], index: int, after: bool, name: str, more: bool) -> float | Order | None:
    """The `_ord` (column `name`) for a row inserted right after / before rows[index]; `more`: rows
    beyond the loaded ones wait. Below a row with no `_ord`: past the highest. Above one: none, the
    new row's `_ord` stays empty."""
    value = new_order(rows, index, after, name)
    if value is None:
        return Order.LAST if after else None
    if after and index == len(rows) - 1 and more:
        return Order.NEEDS_MORE
    return value


def order_descending(schema: list[dict], name: str, sorts: list | None) -> bool | None:
    """Whether rows sorted by `sorts` go down the `_ord` column `name` (True) or up (False); None = not
    sorted by it first."""
    first = (sorts or [None])[0]
    column = next((p for p in schema if p["name"] == name), None)
    if not first or not column or first.get("property") != column["id"]:
        return None
    return first.get("direction") == "descending"


def _whole_as_int(number: float) -> int | float:
    return int(number) if float(number).is_integer() else number


def order_moves(values: list[float | None], index: int, step: int, descending: bool = False) -> dict[int, int | float] | None:
    """New order values ({row index before the move: value}) that move the row at `index` one place up
    (step -1) or down (step 1), in rows sorted by those values; None at the edge. Two different values
    swap; equal or empty ones (empty sorts last) get values that put the pair in the new order."""
    other = index + step
    if not 0 <= other < len(values):
        return None
    if descending:  # mirrored: work on the negated values, which go up as the rows go down
        moves = order_moves([None if v is None else -v for v in values], index, step)
        return {i: _whole_as_int(-v) for i, v in moves.items()}
    top, bottom = min(index, other), max(index, other)
    above, below = values[top], values[bottom]
    if above is not None and below is not None and above != below:
        return {top: _whole_as_int(below), bottom: _whole_as_int(above)}
    known = [v for v in (above, below) if v is not None]
    first = known[0] if known else max((v for v in values[:top] if v is not None), default=0) + 1
    after = next((v for v in values[bottom + 1:] if v is not None and v > first), None)
    second = (first + after) / 2 if after is not None else first + 1
    return {bottom: _whole_as_int(first), top: _whole_as_int(second)}  # the lower row goes on top


def move_row(rows: list[dict], index: int, step: int, descending: bool, name: str) -> list[tuple[str, int | float]] | None:
    """Move the row at `index` one place by giving it and its neighbour new values in the `_ord` column
    `name`, in place; returns the (row ID, value) pairs to save, or None at the edge."""
    moves = order_moves([_number(row, name) for row in rows], index, step, descending)
    if moves is None:
        return None
    for i, value in moves.items():
        rows[i]["properties"][name]["number"] = value
    rows[index], rows[index + step] = rows[index + step], rows[index]
    return [(rows[i]["id"], value) for i, value in ((index + step, moves[index]), (index, moves[index + step]))]
