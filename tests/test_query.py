import pytest

from notion_tui.query import QueryError, combine, parse, parse_date, tokenize

SCHEMA = [
    {"id": "title", "name": "Name", "type": "title", "options": []},
    {"id": "]kno", "name": "Priority ", "type": "select", "options": ["1", "2", "3"]},
    {"id": "~YDn", "name": "Area", "type": "select", "options": ["Other ", "Work ", "Home"]},
    {"id": "st", "name": "Status", "type": "status", "options": ["Not started", "In progress", "Done"]},
    {"id": "tg", "name": "Tags", "type": "multi_select", "options": ["red", "blue", "big red"]},
    {"id": "=cGt", "name": "Due date", "type": "date", "options": []},
    {"id": "'Y6<", "name": "Date Created", "type": "created_time", "options": []},
    {"id": "est", "name": "Estimate", "type": "number", "options": []},
    {"id": "dn", "name": "Done?", "type": "checkbox", "options": []},
    {"id": "nt", "name": "Notes", "type": "rich_text", "options": []},
    {"id": "who", "name": "Assignee", "type": "people", "options": []},
    {"id": "rel", "name": "Related", "type": "relation", "options": []},
]


def test_empty():
    assert parse("", SCHEMA) == []
    assert parse("   ", SCHEMA) == []


def test_bare_words_are_title_contains():
    assert parse('milk -eggs "two words"', SCHEMA) == [
        {"property": "title", "title": {"contains": "milk"}},
        {"property": "title", "title": {"does_not_contain": "eggs"}},
        {"property": "title", "title": {"contains": "two words"}},
    ]


def test_select_or_and_negation():
    assert parse("area:work,home", SCHEMA) == [
        {"or": [
            {"property": "~YDn", "select": {"equals": "Work "}},
            {"property": "~YDn", "select": {"equals": "Home"}},
        ]}
    ]
    assert parse("-area:work,home", SCHEMA) == [
        {"property": "~YDn", "select": {"does_not_equal": "Work "}},
        {"property": "~YDn", "select": {"does_not_equal": "Home"}},
    ]


def test_property_prefix_and_case():
    assert parse("PRI:1", SCHEMA) == [{"property": "]kno", "select": {"equals": "1"}}]
    with pytest.raises(QueryError, match="ambiguous"):
        parse("d:x", SCHEMA)  # Due date, Date Created, Done?
    with pytest.raises(QueryError, match="no property"):
        parse("zzz:1", SCHEMA)


def test_status_option_prefix():
    assert parse("status:in", SCHEMA) == [{"property": "st", "status": {"equals": "In progress"}}]
    with pytest.raises(QueryError, match="no option"):
        parse("status:nope", SCHEMA)


def test_quoted_names_and_values():
    assert parse('"due date">=2024 tags:"big red"', SCHEMA) == [
        {"property": "=cGt", "date": {"on_or_after": "2024-01-01"}},
        {"property": "tg", "multi_select": {"contains": "big red"}},
    ]


def test_multi_select():
    assert parse("-tags:red", SCHEMA) == [{"property": "tg", "multi_select": {"does_not_contain": "red"}}]


def test_text_contains_and_equals():
    assert parse("notes~foo -notes~bar notes=x", SCHEMA) == [
        {"property": "nt", "rich_text": {"contains": "foo"}},
        {"property": "nt", "rich_text": {"does_not_contain": "bar"}},
        {"property": "nt", "rich_text": {"equals": "x"}},
    ]
    with pytest.raises(QueryError, match="doesn't work"):
        parse("notes>3", SCHEMA)


def test_number():
    assert parse("est>=2 est<5.5 -est=3", SCHEMA) == [
        {"property": "est", "number": {"greater_than_or_equal_to": 2}},
        {"property": "est", "number": {"less_than": 5.5}},
        {"property": "est", "number": {"does_not_equal": 3}},
    ]
    assert parse("-est>2", SCHEMA) == [{"property": "est", "number": {"less_than_or_equal_to": 2}}]
    with pytest.raises(QueryError, match="isn't a number"):
        parse("est>abc", SCHEMA)


def test_dates():
    assert parse_date("2024") == (parse_date("2024-01")[0], parse_date("2025-01")[0])
    assert parse_date("2024-12")[1].isoformat() == "2025-01-01"
    assert parse_date("2024-02-29")[1].isoformat() == "2024-03-01"
    assert parse("due<2024 due<=2024-03 due>2024-03-05", SCHEMA) == [
        {"property": "=cGt", "date": {"before": "2024-01-01"}},
        {"property": "=cGt", "date": {"before": "2024-04-01"}},
        {"property": "=cGt", "date": {"on_or_after": "2024-03-06"}},
    ]
    assert parse("due=2024-03", SCHEMA) == [
        {"property": "=cGt", "date": {"on_or_after": "2024-03-01"}},
        {"property": "=cGt", "date": {"before": "2024-04-01"}},
    ]
    assert parse("-due:2024", SCHEMA) == [
        {"or": [
            {"property": "=cGt", "date": {"before": "2024-01-01"}},
            {"property": "=cGt", "date": {"on_or_after": "2025-01-01"}},
        ]}
    ]
    assert parse('"date created">=2026-10', SCHEMA) == [
        {"property": "'Y6<", "created_time": {"on_or_after": "2026-10-01"}}
    ]
    with pytest.raises(QueryError, match="bad date"):
        parse("due<2024-13", SCHEMA)
    with pytest.raises(QueryError, match="bad date"):
        parse("due<tomorrow", SCHEMA)


def test_checkbox():
    assert parse("done?:yes", SCHEMA) == [{"property": "dn", "checkbox": {"equals": True}}]
    assert parse("-done?:yes", SCHEMA) == [{"property": "dn", "checkbox": {"equals": False}}]
    with pytest.raises(QueryError, match="checkbox"):
        parse("done?:maybe", SCHEMA)


def test_people_by_name():
    people = {"Michael Shpilt": "u1", "Dana": "u2"}
    assert parse("assignee:michael", SCHEMA, people) == [{"property": "who", "people": {"contains": "u1"}}]
    with pytest.raises(QueryError, match="no known people"):
        parse("assignee:michael", SCHEMA)


def test_errors():
    with pytest.raises(QueryError, match="unclosed quote"):
        parse('"open', SCHEMA)
    with pytest.raises(QueryError, match="missing value"):
        parse("area:", SCHEMA)
    with pytest.raises(QueryError, match="can't filter"):
        parse("related:x", SCHEMA)
    with pytest.raises(QueryError, match="commas"):
        parse("est>1,2", SCHEMA)
    with pytest.raises(QueryError, match="missing property name"):
        parse(":x", SCHEMA)


def test_tokenize_keeps_quotes():
    assert tokenize('a "b c":d  e') == ["a", '"b c":d', "e"]


def test_combine():
    view = {"property": "x", "checkbox": {"equals": True}}
    term = {"property": "y", "select": {"equals": "a"}}
    assert combine(None, []) is None
    assert combine(view, []) == view
    assert combine(None, [term]) == term
    assert combine(view, [term]) == {"and": [view, term]}
    assert combine({"and": [view]}, [term]) == {"and": [view, term]}
    assert combine({"or": [view, term]}, [term]) == {"and": [{"or": [view, term]}, term]}


def test_property_word_and_substring_match():
    assert parse("created>=2026", SCHEMA) == [{"property": "'Y6<", "created_time": {"on_or_after": "2026-01-01"}}]
    assert parse("sign:x", SCHEMA, {"x": "u9"}) == [{"property": "who", "people": {"contains": "u9"}}]  # Assignee
    with pytest.raises(QueryError, match="ambiguous"):
        parse("ate:2024", SCHEMA)  # substring of Due date and Date Created


def test_comma_dates_merge_and_nesting():
    # back-to-back periods become one range: no nested or/and
    assert parse("due:2024-01,2024-02", SCHEMA) == [
        {"property": "=cGt", "date": {"on_or_after": "2024-01-01"}},
        {"property": "=cGt", "date": {"before": "2024-03-01"}},
    ]
    alone = parse("due:2024,2026", SCHEMA)
    assert combine(None, alone) == alone[0]  # or → and: two levels, fine on its own
    with pytest.raises(QueryError, match="too complex"):
        combine(None, parse("due:2024,2026 area:work", SCHEMA))
    with pytest.raises(QueryError, match="too complex"):
        combine({"property": "x", "checkbox": {"equals": True}}, alone)


def test_combine_rejects_deep_view_filter_plus_terms():
    deep = {"or": [{"and": [{"property": "a", "checkbox": {"equals": True}}]}, {"property": "b", "checkbox": {"equals": True}}]}
    assert combine(deep, []) == deep
    with pytest.raises(QueryError, match="too complex"):
        combine(deep, parse("milk", SCHEMA))


def test_empty_and_not_empty():
    assert parse("area:empty", SCHEMA) == [{"property": "~YDn", "select": {"is_empty": True}}]
    assert parse("-due:EMPTY", SCHEMA) == [{"property": "=cGt", "date": {"is_not_empty": True}}]
    assert parse("rel:empty", SCHEMA) == [{"property": "rel", "relation": {"is_empty": True}}]
    assert parse("assignee=empty", SCHEMA) == [{"property": "who", "people": {"is_empty": True}}]
    assert parse("done?:empty", SCHEMA) == [{"property": "dn", "checkbox": {"equals": False}}]
    assert parse('notes:"empty"', SCHEMA) == [{"property": "nt", "rich_text": {"contains": "empty"}}]
    with pytest.raises(QueryError, match="not Estimate<empty"):
        parse("est<empty", SCHEMA)
