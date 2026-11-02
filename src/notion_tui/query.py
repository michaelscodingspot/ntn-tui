"""The database query line: parse it into a Notion data-source filter. Pure, no I/O.

Syntax: space-separated terms, all of which must match (AND). A leading `-` negates a term.

    prop:v1,v2    select / status / multi_select / people / checkbox (yes/no); comma = OR.
                  On text and number properties `:` means contains / equals.
    prop~text     text contains (title, rich_text, url, email, phone_number)
    prop=v        equals (text, number, date)
    prop<v  prop>v  prop<=v  prop>=v
                  number and date comparisons; dates are YYYY, YYYY-MM or YYYY-MM-DD
    word          title contains word

Property names are case-insensitive and may be shortened to an unambiguous prefix.
Names or values with spaces (or with : ~ < > = ,) go in double quotes: "due date">=2024 area:"side project".
"""

from __future__ import annotations

from datetime import date

TEXT = {"title", "rich_text", "url", "email", "phone_number"}
DATES = {"date", "created_time", "last_edited_time"}
CHOICES = {"select", "status", "multi_select"}
OPERATORS = ("<=", ">=", ":", "~", "<", ">", "=")
_TRUE = {"yes", "true", "1", "checked", "y", "x"}
_FALSE = {"no", "false", "0", "unchecked", "n"}


class QueryError(ValueError):
    """The query line can't be turned into a filter; the message says why."""


def tokenize(text: str) -> list[str]:
    """Split on whitespace outside double quotes. Quotes are kept, so names and values can be unquoted later."""
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            current.append(ch)
        elif ch.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if quoted:
        raise QueryError("unclosed quote")
    if current:
        tokens.append("".join(current))
    return tokens


def _split_unquoted(text: str, sep: str) -> list[str]:
    parts, current, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif ch == sep and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def _unquote(text: str) -> str:
    return text.replace('"', "")


def split_term(token: str) -> tuple[bool, str | None, str | None, str]:
    """(negated, property name, operator, raw value). Name and operator are None for a bare word."""
    negated = token.startswith("-") and len(token) > 1
    body = token[1:] if negated else token
    quoted = False
    for i, ch in enumerate(body):
        if ch == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        for op in OPERATORS:
            if body.startswith(op, i):
                name = _unquote(body[:i]).strip()
                if not name:
                    raise QueryError(f"missing property name before {op!r} in {token!r}")
                return negated, name, op, body[i + len(op):]
    return negated, None, None, body


def find_property(name: str, properties: list[dict]) -> dict:
    """Case-insensitive exact match, else an unambiguous prefix."""
    needle = name.strip().lower()
    exact = [p for p in properties if p["name"].strip().lower() == needle]
    if exact:
        return exact[0]
    prefixed = [p for p in properties if p["name"].strip().lower().startswith(needle)]
    if len(prefixed) == 1:
        return prefixed[0]
    if prefixed:
        names = ", ".join(p["name"].strip() for p in prefixed)
        raise QueryError(f"property {name!r} is ambiguous: {names}")
    raise QueryError(f"no property {name!r} (quote text that contains : ~ < > =)")


def _find_option(value: str, options: list[str], prop: dict) -> str:
    needle = value.strip().lower()
    exact = [o for o in options if o.strip().lower() == needle]
    if exact:
        return exact[0]
    prefixed = [o for o in options if o.strip().lower().startswith(needle)]
    if len(prefixed) == 1:
        return prefixed[0]
    label = prop["name"].strip()
    if prefixed:
        raise QueryError(f"{value!r} is ambiguous for {label}: {', '.join(o.strip() for o in prefixed)}")
    shown = ", ".join(o.strip() for o in options) or "none"
    raise QueryError(f"{label} has no option {value!r} (options: {shown})")


def parse_date(value: str) -> tuple[date, date]:
    """(first day, first day after) of a YYYY, YYYY-MM or YYYY-MM-DD period."""
    parts = value.strip().split("-")
    try:
        numbers = [int(p) for p in parts]
        if len(parts) == 1 and len(parts[0]) == 4:
            return date(numbers[0], 1, 1), date(numbers[0] + 1, 1, 1)
        if len(parts) == 2:
            year, month = numbers
            start = date(year, month, 1)
            return start, date(year + month // 12, month % 12 + 1, 1)
        if len(parts) == 3:
            start = date(*numbers)
            return start, date.fromordinal(start.toordinal() + 1)
    except ValueError:
        pass
    raise QueryError(f"bad date {value!r}: use YYYY, YYYY-MM or YYYY-MM-DD")


def _number(value: str, prop: dict) -> float | int:
    try:
        number = float(value)
    except ValueError:
        raise QueryError(f"{prop['name'].strip()} is a number property; {value!r} isn't a number") from None
    return int(number) if number.is_integer() else number


def _cond(prop: dict, condition: dict) -> dict:
    return {"property": prop["id"], prop["type"]: condition}


def _any(filters: list[dict]) -> list[dict]:
    """OR of filters, as a list to AND into the top level."""
    return filters if len(filters) == 1 else [{"or": filters}]


def _date_term(prop: dict, op: str, value: str, negated: bool) -> list[dict]:
    if negated:
        op = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}.get(op, op)
    start, after = parse_date(value)
    lo, hi = start.isoformat(), after.isoformat()
    match op:
        case "<":
            return [_cond(prop, {"before": lo})]
        case "<=":
            return [_cond(prop, {"before": hi})]
        case ">":
            return [_cond(prop, {"on_or_after": hi})]
        case ">=":
            return [_cond(prop, {"on_or_after": lo})]
    # = or : — inside the period, or (negated) outside it
    if negated:
        return [{"or": [_cond(prop, {"before": lo}), _cond(prop, {"on_or_after": hi})]}]
    return [_cond(prop, {"on_or_after": lo}), _cond(prop, {"before": hi})]


def _term(prop: dict, op: str, raw: str, negated: bool, people: dict[str, str]) -> list[dict]:
    kind, label = prop["type"], prop["name"].strip()
    values = [_unquote(v).strip() for v in _split_unquoted(raw, ",")]
    values = [v for v in values if v]
    if not values:
        raise QueryError(f"missing value after {label}{op}")
    if len(values) > 1 and op not in (":", "="):
        raise QueryError(f"commas (OR) only work with : or =, not {label}{op}")

    def unsupported() -> QueryError:
        hint = {"select": ":", "status": ":", "multi_select": ":", "checkbox": ":", "people": ":",
                "number": "= < > <= >=", "date": "= < > <= >="}.get(kind, ": ~ =")
        return QueryError(f"{op!r} doesn't work on {kind} property {label}; use {hint}")

    if kind in TEXT:
        if op not in (":", "~", "="):
            raise unsupported()
        key = ("does_not_equal" if negated else "equals") if op == "=" else ("does_not_contain" if negated else "contains")
        filters = [_cond(prop, {key: v}) for v in values]
        return filters if negated else _any(filters)

    if kind == "number":
        if op == "~":
            raise unsupported()
        if op in (":", "="):
            key = "does_not_equal" if negated else "equals"
            filters = [_cond(prop, {key: _number(v, prop)}) for v in values]
            return filters if negated else _any(filters)
        if negated:
            op = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}[op]
        key = {"<": "less_than", "<=": "less_than_or_equal_to", ">": "greater_than", ">=": "greater_than_or_equal_to"}[op]
        return [_cond(prop, {key: _number(values[0], prop)})]

    if kind in DATES:
        if op == "~":
            raise unsupported()
        if len(values) == 1:
            return _date_term(prop, op, values[0], negated)
        if negated:  # outside every period
            return [f for v in values for f in _date_term(prop, op, v, True)]
        return [{"or": [{"and": _date_term(prop, op, v, False)} for v in values]}]

    if kind in CHOICES:
        if op not in (":", "="):
            raise unsupported()
        names = [_find_option(v, prop.get("options") or [], prop) for v in values]
        if kind == "multi_select":
            key = "does_not_contain" if negated else "contains"
        else:
            key = "does_not_equal" if negated else "equals"
        filters = [_cond(prop, {key: n}) for n in names]
        return filters if negated else _any(filters)

    if kind == "checkbox":
        if op not in (":", "=") or len(values) > 1:
            raise unsupported()
        word = values[0].lower()
        if word not in _TRUE | _FALSE:
            raise QueryError(f"{label} is a checkbox: use {label}:yes or {label}:no")
        return [_cond(prop, {"equals": (word in _TRUE) != negated})]

    if kind == "people":
        if op not in (":", "="):
            raise unsupported()
        ids = [_find_option(v, sorted(people), prop) for v in values] if people else None
        if not ids:
            raise QueryError(f"no known people for {label} yet (names come from the rows shown)")
        key = "does_not_contain" if negated else "contains"
        filters = [_cond(prop, {key: people[n]}) for n in ids]
        return filters if negated else _any(filters)

    raise QueryError(f"can't filter on {kind} property {label}")


def parse(text: str, properties: list[dict], people: dict[str, str] | None = None) -> list[dict]:
    """Filters (to be ANDed) for a query line. Raises QueryError with a message for the user.

    `properties` is the data source schema: [{id, name, type, options}].
    `people` maps person names to user IDs, for people properties.
    """
    title = next((p for p in properties if p["type"] == "title"), None)
    filters: list[dict] = []
    for token in tokenize(text):
        negated, name, op, raw = split_term(token)
        if name is None:
            word = _unquote(raw).strip()
            if not word:
                continue
            if title is None:
                raise QueryError("this database has no title property")
            filters.append(_cond(title, {"does_not_contain" if negated else "contains": word}))
            continue
        filters += _term(find_property(name, properties), op, raw, negated, people or {})
    return filters


def combine(view_filter: dict | None, filters: list[dict]) -> dict | None:
    """A view's filter AND the query line's filters, flattened into one top-level "and"."""
    parts: list[dict] = []
    if view_filter:
        parts += view_filter["and"] if list(view_filter) == ["and"] else [view_filter]
    parts += filters
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"and": parts}
