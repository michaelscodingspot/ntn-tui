"""Modal dialogs: trash confirmation, options, new page, the database sort picker, the link picker,
the repo picker of a Claude session, and the editors that open over a cell of the database grid."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.geometry import Region
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    RadioButton,
    RadioSet,
    SelectionList,
)

from . import narrow
from .database import sort_level_name
from .links import Link, notion_id
from .new_page import Location, NewPage
from .ntn import NtnError
from .options import DATABASE_ENTRIES, Options
from .rows import check_option


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
                yield Button("Delete (y)", variant="error", id="yes")
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


_NARROW_LABEL = "Narrow width: 70% on a screen-wide window (Ctrl+Alt+W)"


class OptionsScreen(ModalScreen[None]):
    """Options dialog. Each change is saved at once; the list is refreshed on close."""

    AUTO_FOCUS = "RadioSet"
    BINDINGS = [Binding("escape,comma,q", "close", "close")]
    DEFAULT_CSS = """
    OptionsScreen { align: center middle; }
    #options { width: 72; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #options Label { width: 100%; }
    #options RadioSet { width: 100%; margin: 1 0; }
    #options Checkbox { width: 100%; margin-bottom: 1; }
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
            yield Label("[b]Window[/b]")
            available = narrow.available()
            yield Checkbox(_NARROW_LABEL, value=narrow.is_on(), disabled=not available, id="narrow")
            if not available:
                yield Label("Only in the window `nui` opens", classes="hint", markup=False)
            yield Label("↑/↓ move · tab next · enter choose · esc close", classes="hint")

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

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not narrow.set_on(event.value):
            with self.prevent(Checkbox.Changed):
                event.checkbox.value = not event.value
            self.notify("Could not reach the kitty window", severity="warning")

    def action_close(self) -> None:
        self.dismiss(None)


class PickList(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
    ]


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
        await self._show_locations(self.nearby)

    async def _show_locations(self, locations: list[Location], empty: str = "") -> None:
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
            await self._show_locations(self.nearby)
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


class RepoPickScreen(ModalScreen[str | None]):
    """Choose the repo a Claude session runs in. Returns a label from `labels`, or the folder typed
    for "other"."""

    AUTO_FOCUS = "PickList"
    BINDINGS = [Binding("escape", "cancel", "cancel")]
    DEFAULT_CSS = """
    RepoPickScreen { align: center middle; }
    #repo { width: 60; height: auto; max-height: 80%; padding: 1 2; border: round $accent; background: $surface; }
    #repo PickList { height: auto; max-height: 20; background: transparent; }
    #repo ListItem { padding: 0 1; }
    #repo Input { display: none; margin-top: 1; }
    #repo .hint { color: $text-muted; margin-top: 1; }
    """
    OTHER = "other (specify)"

    def __init__(self, labels: list[str]) -> None:
        super().__init__()
        self.labels = labels

    def compose(self) -> ComposeResult:
        with Vertical(id="repo"):
            yield PickList(*(ListItem(Label(label)) for label in [*self.labels, self.OTHER]))
            yield Input(placeholder="folder in /c/, or a full path")
            yield Label("j/k move · enter choose · esc cancel", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#repo").border_title = "Run Claude in"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        index = event.list_view.index or 0
        if index < len(self.labels):
            self.dismiss(self.labels[index])
            return
        box = self.query_one(Input)
        box.display = True
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip():
            self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class CellTextScreen(ModalScreen):
    """A one-line editor over a grid cell. Dismisses with what `parse` makes of the text, or None
    on esc or when nothing changed. `parse` raises ValueError to keep the editor open with a message."""

    BINDINGS = [
        Binding("enter", "save", "save"),  # the input takes the key; listed so the footer shows it
        Binding("escape", "cancel", "cancel"),
    ]
    DEFAULT_CSS = """
    CellTextScreen { background: transparent; }
    #cell-input { height: 1; border: none; padding: 0 1; background: $boost; }
    #cell-input:focus { border: none; background: $boost; }
    """
    MIN_WIDTH = 30

    def __init__(self, value: str, cell: Region, parse: Callable[[str], object]) -> None:
        super().__init__()
        self.value = value
        self.cell = cell
        self.parse = parse

    def compose(self) -> ComposeResult:
        yield Input(self.value, select_on_focus=False, id="cell-input")
        yield Footer()

    def on_mount(self) -> None:
        box = self.query_one(Input)
        width = max(self.cell.width, self.MIN_WIDTH)
        box.styles.width = width
        box.styles.offset = (max(min(self.cell.x, self.size.width - width), 0), self.cell.y)
        box.cursor_position = len(self.value)

    def action_save(self) -> None:
        self.query_one(Input).action_submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value == self.value:
            self.dismiss(None)
            return
        try:
            self.dismiss(self.parse(event.value))
        except ValueError as err:
            self.notify(str(err), severity="warning")

    def action_cancel(self) -> None:
        self.dismiss(None)


class _PickList(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("enter", "select", "choose"),
    ]


class _TickList(SelectionList):
    BINDINGS = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("space", "select", "tick"),
        Binding("enter", "screen.done", "done"),
    ]


_EMPTY = "(empty)"


class CellPickScreen(ModalScreen[list[str] | None]):
    """A pick list under a grid cell: one choice, or several with `multi`. Dismisses with the chosen
    names ([] = cleared), or None on esc. With `can_add`, `a` adds a new option by name."""

    BINDINGS = [
        Binding("a", "add", "new option"),
        Binding("escape", "cancel", "cancel"),
    ]
    DEFAULT_CSS = """
    CellPickScreen { background: transparent; }
    CellPickScreen #picker { width: auto; min-width: 24; max-width: 50; height: auto; max-height: 14; border: round $accent; background: $surface; }
    #pick-list, #pick-list:focus { width: auto; min-width: 22; height: auto; max-height: 12; border: none; background: transparent; }
    #new-option, #new-option:focus { height: 1; border: none; padding: 0 1; background: $boost; }
    #new-option { display: none; }
    """

    def __init__(self, options: list[str], chosen: list[str], cell: Region, multi: bool, can_add: bool) -> None:
        super().__init__()
        self.options = options + [name for name in chosen if name not in options]
        self.chosen = chosen
        self.cell = cell
        self.multi = multi
        self.can_add = can_add

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            if self.multi:
                yield _TickList(*[(name, name, name in self.chosen) for name in self.options], id="pick-list")
            else:
                yield _PickList(*self.options, _EMPTY, id="pick-list")
            yield Input(placeholder="new option", id="new-option")
        yield Footer()

    def on_mount(self) -> None:
        if not self.multi and self.chosen:
            self.query_one(_PickList).highlighted = self.options.index(self.chosen[0])
        rows = min(len(self.options) + (0 if self.multi else 1), 12) + 3  # the list, its border, the footer
        below = self.cell.bottom
        y = below if below + rows <= self.size.height else max(self.cell.y - rows + 1, 0)
        self.query_one("#picker").styles.offset = (max(min(self.cell.x, self.size.width - 50), 0), y)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "add":
            return self.can_add and not self.query_one("#new-option").display
        return True

    def action_add(self) -> None:
        box = self.query_one("#new-option", Input)
        box.display = True
        box.focus()
        self.refresh_bindings()

    def _close_new_option(self) -> None:
        box = self.query_one("#new-option", Input)
        box.value = ""
        box.display = False
        self.query_one(_TickList if self.multi else _PickList).focus()
        self.refresh_bindings()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        try:
            name = check_option(event.value)
        except ValueError as err:
            self.notify(str(err), severity="warning")
            return
        if not name:
            self._close_new_option()
        elif self.multi:
            ticks = self.query_one(_TickList)
            if name not in self.options:
                self.options.append(name)
                ticks.add_option((name, name, True))
            else:
                ticks.select(name)
            self._close_new_option()
        else:
            self.dismiss([name])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self.multi:
            event.stop()
            name = str(event.option.prompt)
            self.dismiss([] if name == _EMPTY else [name])

    def action_done(self) -> None:
        picked = self.query_one(_TickList).selected
        self.dismiss([name for name in self.options if name in picked])

    def action_cancel(self) -> None:
        if self.query_one("#new-option").display:
            self._close_new_option()
        else:
            self.dismiss(None)
