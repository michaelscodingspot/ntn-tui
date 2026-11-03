"""Modal dialogs: trash confirmation, options, the database sort picker and the link picker."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, RadioButton, RadioSet

from .database import sort_level_name
from .links import Link, notion_id
from .options import DATABASE_ENTRIES, Options


class ConfirmScreen(ModalScreen[bool]):
    AUTO_FOCUS = "#no"
    BINDINGS = [
        Binding("y", "answer(True)", "yes"),
        Binding("n,escape", "answer(False)", "no"),
    ]
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 60; height: auto; padding: 1 2; border: round $error; background: $surface; }
    #dialog Label { width: 100%; margin-bottom: 1; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, markup=False)
            with Horizontal(id="buttons"):
                yield Button("Trash (y)", variant="error", id="yes")
                yield Button("Cancel (n)", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


_ENTRY_LABELS = {
    "yes": "yes: always",
    "never": "never",
    "show-non-empty": "show-non-empty: only entries with page content (default)",
}


class OptionsScreen(ModalScreen[None]):
    """Options dialog. Each change is saved at once; the list is refreshed on close."""

    AUTO_FOCUS = "RadioSet"
    BINDINGS = [Binding("escape,comma,q", "close", "close")]
    DEFAULT_CSS = """
    OptionsScreen { align: center middle; }
    #options { width: 72; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #options Label { width: 100%; }
    #options RadioSet { width: 100%; margin: 1 0; }
    #options .hint { color: $text-muted; }
    """

    def __init__(self, options: Options) -> None:
        super().__init__()
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="options"):
            yield Label("[b]Show pages that are database entries[/b]")
            with RadioSet(id="database_entries"):
                for value in DATABASE_ENTRIES:
                    yield RadioButton(_ENTRY_LABELS[value], value=value == self.options.database_entries, name=value)
            yield Label("↑/↓ move · enter choose · esc close", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#options").border_title = "Options"
        # RadioSet starts its highlight on the first button; start it on the saved choice instead.
        radio_set = self.query_one("#database_entries", RadioSet)
        for _ in range(max(radio_set.pressed_index, 0)):
            radio_set.action_next_button()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "database_entries" and event.pressed.name:
            self.options.database_entries = event.pressed.name
            self.options.save()

    def action_close(self) -> None:
        self.dismiss(None)


# Property types a database can be sorted by.
_UNSORTABLE_TYPES = {"relation", "rollup", "files", "button"}


class PickList(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
    ]


class PropertyPickScreen(ModalScreen[str | None]):
    """Choose one property to sort by. Returns its ID."""

    AUTO_FOCUS = "PickList"
    BINDINGS = [Binding("escape,q", "cancel", "cancel")]
    DEFAULT_CSS = """
    PropertyPickScreen { align: center middle; }
    #pick { width: 50; height: auto; max-height: 80%; padding: 1 2; border: round $accent; background: $surface; }
    #pick PickList { height: auto; max-height: 20; background: transparent; }
    #pick ListItem { padding: 0 1; }
    #pick .hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, properties: list[dict]) -> None:
        super().__init__()
        self.properties = properties

    def compose(self) -> ComposeResult:
        with Vertical(id="pick"):
            yield PickList(*(ListItem(Label(Text.assemble(p["name"].strip(), (f"  {p['type']}", "dim")))) for p in self.properties))
            yield Label("j/k move · enter choose · esc cancel", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#pick").border_title = "Sort by"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.dismiss(self.properties[event.list_view.index or 0]["id"])

    def action_cancel(self) -> None:
        self.dismiss(None)


class SortScreen(ModalScreen[list | None]):
    """Edit a database's sort levels. Returns the new sorts list, or None when cancelled."""

    AUTO_FOCUS = "PickList"
    BINDINGS = [
        Binding("enter", "apply", "apply", priority=True),
        Binding("escape,q", "cancel", "cancel"),
        Binding("a,o", "add", "add"),
        Binding("x,d", "remove", "remove"),
        Binding("space,t", "flip", "flip"),
        Binding("K", "move(-1)", "move up"),
        Binding("J", "move(1)", "move down"),
        Binding("c", "clear", "clear"),
    ]
    DEFAULT_CSS = """
    SortScreen { align: center middle; }
    #sort { width: 60; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #sort PickList { height: auto; max-height: 12; background: transparent; }
    #sort ListItem { padding: 0 1; }
    #sort .empty { color: $text-muted; }
    #sort .hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, sorts: list, schema: list[dict]) -> None:
        super().__init__()
        self.levels = [dict(level) for level in sorts]
        self.schema = schema

    def compose(self) -> ComposeResult:
        with Vertical(id="sort"):
            yield PickList()
            yield Label("(no sort: rows come in Notion's default order)", classes="empty")
            yield Label(
                "j/k move · a add · x remove · space flip ↑/↓\nJ/K move level down/up · c clear · enter apply · esc cancel",
                classes="hint",
            )

    async def on_mount(self) -> None:
        self.query_one("#sort").border_title = "Sort"
        await self._render_levels(0)

    def _label(self, i: int, level: dict) -> str:
        name = sort_level_name(level, self.schema)
        return f"{i + 1}. {name}  {'↓ descending' if level.get('direction') == 'descending' else '↑ ascending'}"

    async def _render_levels(self, index: int) -> None:
        pick = self.query_one(PickList)
        await pick.clear()
        await pick.extend(ListItem(Label(self._label(i, level), markup=False)) for i, level in enumerate(self.levels))
        self.query_one(".empty").display = not self.levels
        if self.levels:
            pick.index = max(0, min(index, len(self.levels) - 1))

    def _index(self) -> int | None:
        index = self.query_one(PickList).index
        return index if self.levels and index is not None else None

    def action_add(self) -> None:
        used = {level.get("property") for level in self.levels}
        choices = [p for p in self.schema if p["type"] not in _UNSORTABLE_TYPES and p["id"] not in used]
        if not choices:
            self.notify("No more properties to sort by.", severity="warning")
            return

        async def picked(prop_id: str | None) -> None:
            if prop_id:
                self.levels.append({"property": prop_id, "direction": "ascending"})
                await self._render_levels(len(self.levels) - 1)

        self.app.push_screen(PropertyPickScreen(choices), picked)

    async def action_remove(self) -> None:
        index = self._index()
        if index is not None:
            del self.levels[index]
            await self._render_levels(index)

    async def action_flip(self) -> None:
        index = self._index()
        if index is not None:
            level = self.levels[index]
            level["direction"] = "ascending" if level.get("direction") == "descending" else "descending"
            await self._render_levels(index)

    async def action_move(self, step: int) -> None:
        index = self._index()
        if index is None or not 0 <= index + step < len(self.levels):
            return
        self.levels[index], self.levels[index + step] = self.levels[index + step], self.levels[index]
        await self._render_levels(index + step)

    async def action_clear(self) -> None:
        self.levels = []
        await self._render_levels(0)

    def action_apply(self) -> None:
        self.dismiss(self.levels)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LinkPickScreen(ModalScreen[Link | None]):
    """Choose one of the page's links. Returns it."""

    AUTO_FOCUS = "PickList"
    BINDINGS = [Binding("escape,q,l", "cancel", "cancel")]
    DEFAULT_CSS = """
    LinkPickScreen { align: center middle; }
    #links { width: 80%; max-width: 100; height: auto; max-height: 80%; padding: 1 2; border: round $accent; background: $surface; }
    #links PickList { height: auto; max-height: 20; background: transparent; }
    #links ListItem { padding: 0 1; }
    #links .hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, links: list[Link]) -> None:
        super().__init__()
        self.links = links

    def compose(self) -> ComposeResult:
        def item(link: Link) -> ListItem:
            where = "Notion" if notion_id(link.url) else link.url
            text = Text.assemble(link.title, (f"  {where}", "dim"), no_wrap=True, overflow="ellipsis")
            return ListItem(Label(text))

        with Vertical(id="links"):
            yield PickList(*(item(link) for link in self.links))
            yield Label("j/k move · enter open · esc cancel", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#links").border_title = "Links"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.dismiss(self.links[event.list_view.index or 0])

    def action_cancel(self) -> None:
        self.dismiss(None)
