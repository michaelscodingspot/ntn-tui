"""Modal dialogs: trash confirmation, options, new page, the database sort picker and the link picker."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    RadioButton,
    RadioSet,
)

from .database import sort_level_name
from .links import Link, notion_id
from .ntn import NtnError, Page
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


class PickList(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
    ]


@dataclass(frozen=True)
class Location:
    """Where a new page goes: inside a page, database or data source, or at the top level."""

    label: str  # "Inside: Notes / Quick Notes"
    parent_kind: str  # page | database | data_source | workspace
    parent_id: str  # "" for the workspace

    @classmethod
    def inside(cls, page: Page, path: str) -> Location:
        """Inside a list item; a database is shown as its data source, so it gets the rows' parent."""
        kind = "data_source" if page.kind == "data_source" else "page"
        return cls(f"Inside: {path}", kind, page.id)


def page_path(page: Page, ancestry: tuple[list[str], tuple[str, str] | None]) -> str:
    """"Notes / Quick Notes" from the cached ancestry; "… / Quick Notes" when it isn't all cached."""
    titles, missing = ancestry
    if missing is not None and page.parent_kind != "workspace":
        titles = ["…"]
    return " / ".join([*titles, page.title])


TOP_LEVEL = Location("Top level of the workspace", "workspace", "")


def nearby_locations(page: Page | None, ancestry: tuple[list[str], tuple[str, str] | None]) -> list[Location]:
    """Where a new page can go: inside the highlighted page, next to it, and always the top level."""
    if page is None:
        return [TOP_LEVEL]
    locations = [Location.inside(page, page_path(page, ancestry))]
    if page.parent_kind in ("page", "database", "data_source"):
        titles, missing = ancestry
        where = " / ".join(titles) if missing is None and titles else "its parent"
        locations.append(Location(f"Next to “{page.title}”: in {where}", page.parent_kind, page.parent_id))
    return [*locations, TOP_LEVEL]


@dataclass(frozen=True)
class NewPage:
    title: str
    location: Location


LOCATION_SEARCH_DEBOUNCE = 0.4


class NewPageScreen(ModalScreen[NewPage | None]):
    """Ask for a new page's title and where in the tree it goes.

    The location list starts with places near the highlighted page; typing in the
    location search replaces it with matching pages and databases (`find` asks Notion).
    """

    AUTO_FOCUS = "#title"
    BINDINGS = [Binding("escape", "back", "back")]
    DEFAULT_CSS = """
    NewPageScreen { align: center middle; }
    #newpage { width: 80; height: auto; max-height: 90%; padding: 1 2; border: round $accent; background: $surface; }
    #newpage Input { margin-bottom: 1; }
    #newpage PickList { height: auto; max-height: 14; background: transparent; }
    #newpage ListItem { padding: 0 1; }
    #newpage .empty, #newpage .hint { color: $text-muted; }
    #newpage .hint { margin-top: 1; }
    """

    def __init__(self, nearby: list[Location], find: Callable[[str], Awaitable[list[Location]]]) -> None:
        super().__init__()
        self.nearby = nearby
        self.find = find  # search text → parent locations; raises NtnError
        self.locations: list[Location] = []
        self._search_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="newpage"):
            yield Label("Title")
            yield Input(placeholder="page title", id="title")
            yield Label("Location")
            yield Input(placeholder="search for another parent page or database…", id="where")
            yield PickList()
            yield Label("", classes="empty")
            yield Label("tab next field · j/k move · enter create · esc clear search / cancel", classes="hint")

    async def on_mount(self) -> None:
        self.query_one("#newpage").border_title = "New page"
        await self._show_locations(self.nearby, "")

    async def _show_locations(self, locations: list[Location], empty: str) -> None:
        self.locations = locations
        pick = self.query_one(PickList)
        await pick.clear()
        await pick.extend(ListItem(Label(loc.label, markup=False)) for loc in locations)
        if locations:
            pick.index = 0
        empty_label = self.query_one(".empty", Label)
        empty_label.update(empty)
        empty_label.display = not locations

    async def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()  # the app treats input changes as its page search
        if event.input.id != "where":
            return
        if self._search_timer:
            self._search_timer.stop()
        self.workers.cancel_group(self, "where")
        query = event.value.strip()
        if not query:
            await self._show_locations(self.nearby, "")
            return
        # Drop the old rows at once, so a stale place can't be picked while the search runs.
        await self._show_locations([], "searching…")
        self._search_timer = self.set_timer(LOCATION_SEARCH_DEBOUNCE, lambda: self.search(query))

    @work(exclusive=True, group="where")
    async def search(self, query: str) -> None:
        try:
            locations, empty = await self.find(query), f"Nothing matches “{query}”."
        except NtnError as err:
            locations, empty = [], f"Search failed: {err}"
        if self.query_one("#where", Input).value.strip() == query:  # else typed on meanwhile
            await self._show_locations(locations, empty)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one(PickList).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        title = self.query_one("#title", Input).value.strip()
        if not title:
            self.notify("Give the page a title first.", severity="warning")
            self.query_one("#title", Input).focus()
            return
        self.dismiss(NewPage(title, self.locations[event.list_view.index or 0]))

    def action_back(self) -> None:
        """Like the main window: esc clears a search first, then closes."""
        where = self.query_one("#where", Input)
        if where.value:
            where.value = ""
            where.focus()
        else:
            self.dismiss(None)


# Property types a database can be sorted by.
_UNSORTABLE_TYPES = {"relation", "rollup", "files", "button"}


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
    """Choose one of the page's links or images. Returns it."""

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
            where = "image" if link.image else "Notion" if notion_id(link.url) else link.url
            text = Text.assemble(link.title, (f"  {where}", "dim"), no_wrap=True, overflow="ellipsis")
            return ListItem(Label(text))

        with Vertical(id="links"):
            yield PickList(*(item(link) for link in self.links))
            yield Label("j/k move · enter open · esc cancel", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#links").border_title = "Links and images"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.dismiss(self.links[event.list_view.index or 0])

    def action_cancel(self) -> None:
        self.dismiss(None)
