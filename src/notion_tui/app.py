"""Layout A: page list on the left, Markdown preview on the right."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Markdown, RadioButton, RadioSet, Static

from . import desktop, ntn
from .cache import MAX_DEPTH, Cache
from .ntn import NtnError, Page
from .options import DATABASE_ENTRIES, Options

PREVIEW_DEBOUNCE = 0.12  # seconds to wait after the cursor stops before loading a preview
SEARCH_DEBOUNCE = 0.4  # seconds to wait after typing stops before asking Notion
LOAD_MORE_MARGIN = 5  # fetch the next batch when the cursor is this close to the end
BODY_CHECKS = 2  # parallel ntn calls when checking which database entries have content

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_PROPERTY = re.compile(r"^'?([^':][^:]*?)'?:\s*(.*)$")
_BR = re.compile(r"<br\s*/?>")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_EMPTY_VALUES = {"", "null", "[]", "''", '""', "false"}


def split_page(md: str, title: str) -> tuple[list[tuple[str, str]], str]:
    """Split `ntn pages get` output into (non-empty properties, Markdown body)."""
    properties: list[tuple[str, str]] = []
    match = _FRONTMATTER.match(md)
    if match:
        for line in match.group(1).splitlines():
            prop = _PROPERTY.match(line)
            if prop:
                key, value = prop.group(1).strip(), prop.group(2).strip().strip("'\"")
                if value not in _EMPTY_VALUES and value != title and key.lower() != "title":
                    if _ISO_DATE.match(value):
                        value = long_date(value)
                    properties.append((key, value))
        md = md[match.end():]
    md = md.replace("<empty-block/>", "")
    md = _BR.sub("  \n", md)
    return properties, md.strip()


# System columns that only add width to a database preview.
_HIDDEN_COLUMN_TYPES = {"created_time", "last_edited_time", "created_by", "last_edited_by", "relation", "rollup", "files", "button", "unique_id"}


def rows_markdown(rows: list[dict], has_more: bool) -> str:
    """A database's rows as a Markdown table: the title column first, then non-empty columns."""
    if not rows:
        return "_(no rows)_"
    columns: list[str] = []
    for row in rows:
        for name, prop in row.get("properties", {}).items():
            if name not in columns and prop.get("type") not in _HIDDEN_COLUMN_TYPES:
                columns.append(name)
    columns.sort(key=lambda name: rows[0]["properties"].get(name, {}).get("type") != "title")
    cells = [[ntn.value_text(row["properties"][c]) if c in row["properties"] else "" for c in columns] for row in rows]
    keep = [i for i, c in enumerate(columns) if i == 0 or any(r[i] for r in cells)]

    def line(values: list[str]) -> str:
        return "| " + " | ".join(v.replace("|", "\\|").replace("\n", " ") for v in values) + " |"

    out = [line([columns[i].strip() for i in keep]), "|" + "---|" * len(keep)]
    out += [line([r[i] for i in keep]) for r in cells]
    if has_more:
        out.append(f"\n_First {len(rows)} rows. Press o to see all in Notion._")
    return "\n".join(out)


def _local(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return None


def long_date(iso: str) -> str:
    dt = _local(iso)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "unknown"


class PageItem(ListItem):
    def __init__(self, page: Page) -> None:
        super().__init__()
        self.page = page

    def compose(self) -> ComposeResult:
        marker = "▦ " if self.page.kind == "data_source" else ""
        yield Label(marker + self.page.title, classes="title", markup=False)


class Breadcrumb(Widget):
    """One line: "Notes / Quick Notes". When too long, the start is cut and replaced by "…"."""

    parts: reactive[tuple[str, ...]] = reactive(())

    def render(self) -> Text:
        text = Text(no_wrap=True)
        for i, part in enumerate(self.parts):
            if i:
                text.append(" / ", style="dim")
            text.append(part, style="bold" if i == len(self.parts) - 1 else "")
        width = self.content_size.width
        if width and text.cell_len > width:
            while text.cell_len > width - 1 and len(text):
                text = text[1:]
            text = Text("…", style="dim") + text
        return text


class PageList(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("g", "first", "top", show=False),
        Binding("G", "last", "bottom", show=False),
    ]

    def action_first(self) -> None:
        if self.children:
            self.index = 0

    def action_last(self) -> None:
        if self.children:
            self.index = len(self.children) - 1


class PreviewScroll(VerticalScroll, can_focus=True):
    BINDINGS = [
        Binding("j", "scroll_down", "down", show=False),
        Binding("k", "scroll_up", "up", show=False),
        Binding("space", "page_down", "page down", show=False),
        Binding("g", "scroll_home", "top", show=False),
        Binding("G", "scroll_end", "bottom", show=False),
    ]


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


class NotionTUI(App):
    TITLE = "notion-tui"
    CSS = """
    #topbar { height: 1; margin: 0 1; }
    #search { width: 1fr; height: 1; border: none; padding: 0 1; background: $surface; }
    #search:focus { background: $boost; }
    #status { width: auto; padding-left: 2; color: $text-muted; }
    #body { height: 1fr; }
    #list-pane { width: 44; border: round $border-blurred; border-title-color: $text-muted; }
    #list-pane:focus-within { border: round $accent; border-title-color: $accent; }
    #preview-pane { width: 1fr; border: round $border-blurred; border-subtitle-color: $text-muted; }
    #preview-pane:focus-within { border: round $accent; }
    #crumbs { height: 1; padding: 0 1; margin-bottom: 1; color: $text; }
    PreviewScroll { padding: 0 1; }
    Screen.full #list-pane { display: none; }
    PageList { background: transparent; scrollbar-size-vertical: 1; }
    PageItem { height: 1; padding: 0 1; }
    PageList > PageItem.-highlight { background: $block-cursor-blurred-background; }
    PageList:focus > PageItem.-highlight { background: $accent; color: $background; text-style: bold; }
    PageItem .title { width: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }
    #preview { background: transparent; }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "search"),
        Binding("e", "edit", "edit"),
        Binding("n", "new", "new"),
        Binding("d", "trash", "trash"),
        Binding("o", "browser", "open in Notion"),
        Binding("r", "refresh", "refresh"),
        Binding("comma", "options", "options"),
        Binding("escape", "back", "back", show=False),
        Binding("question_mark", "help", "help"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, cache: Cache | None = None, options: Options | None = None) -> None:
        super().__init__()
        self.cache = cache or Cache()
        self.options = options or Options.load()
        self.loaded: list[Page] = []  # pages fetched for the current list, before the options filter
        self.shown: list[Page] = []  # pages currently in the list, in order
        self.current: Page | None = None  # highlighted page
        self.query_text = ""  # active search ("" = recent pages)
        self.next_cursor: str | None = None  # Notion cursor for the next batch of the current list
        self._loading = 0
        self._rendered_id: str | None = None
        self._preview_timer: Timer | None = None
        self._search_timer: Timer | None = None

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Input(placeholder="/ search pages…", id="search")
            yield Static("", id="status")
        with Horizontal(id="body"):
            with Vertical(id="list-pane"):
                yield PageList(id="pages")
            with Vertical(id="preview-pane"):
                yield Breadcrumb(id="crumbs")
                with PreviewScroll():
                    yield Markdown("", id="preview")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#list-pane").border_title = "Pages"
        self.query_one(PageList).focus()
        cached = self.cache.recent_pages()
        if cached:
            await self._show_pages(cached)
        self.load_list("")

    # ---------- list ----------

    def _update_status(self) -> None:
        count = f"{len(self.shown)}{'+' if self.next_cursor else ''} pages"
        hidden = len(self.loaded) - len(self.shown)
        if hidden:
            count += f" · {hidden} entries hidden"
        label = f"“{self.query_text}” · {count}" if self.query_text else f"Recent · {count}"
        if self._loading:
            label += " · loading…"
        self.query_one("#status", Static).update(label)

    def _visible(self, pages: list[Page]) -> list[Page]:
        """Apply the database entries option."""
        mode = self.options.database_entries
        if mode == "yes":
            return pages
        if mode == "never":
            return [p for p in pages if not p.is_entry]
        bodies = self.cache.has_body([p.id for p in pages if p.is_entry])
        return [p for p in pages if not p.is_entry or bodies[p.id]]

    def _unchecked_entries(self) -> list[Page]:
        if self.options.database_entries != "show-non-empty":
            return []
        entries = [p for p in self.loaded if p.is_entry]
        bodies = self.cache.has_body([p.id for p in entries])
        return [p for p in entries if bodies[p.id] is None]

    async def _show_pages(self, pages: list[Page], keep_id: str | None = None, fallback_index: int = 0) -> None:
        """Replace the list, keeping the cursor on `keep_id` when it is still present."""
        self.loaded = pages
        await self._render_list(keep_id, fallback_index)
        if self._unchecked_entries():
            self.check_bodies()

    async def _render_list(self, keep_id: str | None = None, fallback_index: int = 0) -> None:
        list_view = self.query_one(PageList)
        pages = self.shown = self._visible(self.loaded)
        await list_view.clear()
        await list_view.extend(PageItem(p) for p in pages)
        if pages:
            ids = [p.id for p in pages]
            list_view.index = ids.index(keep_id) if keep_id in ids else min(fallback_index, len(pages) - 1)
        else:
            self.current = None
            await self._render_message("No pages found.")
        self._update_status()

    async def _append_pages(self, pages: list[Page]) -> None:
        known = {p.id for p in self.loaded}
        new = [p for p in pages if p.id not in known]
        self.loaded.extend(new)
        visible = self._visible(new)
        self.shown.extend(visible)
        await self.query_one(PageList).extend(PageItem(p) for p in visible)
        self._update_status()
        if self._unchecked_entries():
            self.check_bodies()

    @work(exclusive=True, group="bodies")
    async def check_bodies(self) -> None:
        """Find out which database entries have content, then show the ones that do."""
        limit = asyncio.Semaphore(BODY_CHECKS)

        async def check(page: Page) -> bool:
            async with limit:
                try:
                    has_body = await ntn.has_body(page.id)
                except NtnError:
                    return False  # stays unchecked and hidden; tried again on the next list load
                self.cache.put_has_body(page.id, has_body)
                return has_body

        found = await asyncio.gather(*(check(p) for p in self._unchecked_entries()))
        if any(found):
            await self._render_list(keep_id=self.current.id if self.current else None)

    @work(exclusive=True, group="list")
    async def load_list(self, query: str, more: bool = False) -> None:
        """Fetch recent pages (empty query) or search results from Notion."""
        cursor = self.next_cursor if more else None
        self._loading += 1
        self._update_status()
        try:
            pages, next_cursor = await ntn.search(query, cursor)
        except NtnError as err:
            self.notify(str(err), title="ntn search failed", severity="error")
            return
        finally:
            self._loading -= 1
            self._update_status()
        if query != self.query_text:
            return  # the user typed something else meanwhile
        self.cache.upsert_pages(pages)
        self.next_cursor = next_cursor
        if more:
            await self._append_pages(pages)
        else:
            await self._show_pages(pages, keep_id=self.current.id if self.current else None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if not isinstance(event.item, PageItem):
            return
        self.current = event.item.page
        index = event.list_view.index or 0
        if self.next_cursor and not self._loading and index >= len(self.shown) - LOAD_MORE_MARGIN:
            self.load_list(self.query_text, more=True)
        if self._preview_timer:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(PREVIEW_DEBOUNCE, self._show_current)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.screen.add_class("full")
        self.query_one(PreviewScroll).focus()

    # ---------- search ----------

    async def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        self.query_text = query
        self.next_cursor = None
        if self._search_timer:
            self._search_timer.stop()
        # Instant local answer from the cache, then ask Notion once typing pauses.
        if query:
            needle = query.lower()
            local = [p for p in self.cache.recent_pages(limit=2000) if needle in p.title.lower()]
        else:
            local = self.cache.recent_pages()
        await self._show_pages(local, keep_id=self.current.id if self.current else None)
        self._search_timer = self.set_timer(SEARCH_DEBOUNCE, lambda: self.load_list(query))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one(PageList).focus()

    # ---------- preview ----------

    async def _render_message(self, text: str) -> None:
        self._rendered_id = None
        self.query_one("#preview-pane").border_subtitle = ""
        self.query_one(Breadcrumb).parts = ()
        await self.query_one("#preview", Markdown).update(f"_{text}_")

    async def _render_page(self, page: Page, markdown: str | None, note: str = "") -> None:
        self.query_one("#preview-pane").border_subtitle = note
        if markdown is None:
            content = "_loading…_"
        else:
            properties, body = split_page(markdown, page.title)
            content = body or "_(empty page)_"
            if properties:
                content = "  \n".join(f"**{k}:** {v}" for k, v in properties) + "\n\n---\n\n" + content
        await self.query_one("#preview", Markdown).update(content)
        if self._rendered_id != page.id:
            self.query_one(PreviewScroll).scroll_home(animate=False)
            self._rendered_id = page.id
            self._show_path(page)

    # ---------- breadcrumb path ----------

    def _show_path(self, page: Page) -> None:
        """Show the path from the cache at once; look up missing ancestors in the background."""
        titles, missing = self.cache.ancestry(page.id)
        self.query_one(Breadcrumb).parts = (*titles, page.title) if missing is None else (page.title,)
        if missing is not None:
            self.resolve_path(page)

    async def _complete_path(self, page: Page) -> list[str]:
        """Fetch ancestors not cached yet (one ntn call each). Unreachable ones show as "…"."""
        titles, missing = self.cache.ancestry(page.id)
        for _ in range(MAX_DEPTH):
            if missing is None:
                break
            try:
                node = await ntn.get_node(*missing)
            except NtnError:
                return ["…", *titles]  # no access to that ancestor
            self.cache.put_nodes([node])
            titles, missing = self.cache.ancestry(page.id)
        return titles

    @work(exclusive=True, group="path")
    async def resolve_path(self, page: Page) -> None:
        titles = await self._complete_path(page)
        if self.current and self.current.id == page.id:
            self.query_one(Breadcrumb).parts = (*titles, page.title)

    async def _show_current(self) -> None:
        page = self.current
        if page is None:
            return
        cached = self.cache.content(page.id)
        # A database's last_edited doesn't always move when its rows change, so tables are always refetched.
        if cached and cached[1] == page.last_edited and page.kind == "page":
            await self._render_page(page, cached[0])
            self._prefetch_next()
            return
        await self._render_page(page, cached[0] if cached else None, note="refreshing…")
        self.fetch_preview(page)

    @work(exclusive=True, group="preview")
    async def fetch_preview(self, page: Page) -> None:
        try:
            markdown = await self._fetch_content(page)
        except NtnError as err:
            if self.current and self.current.id == page.id:
                await self._render_message(f"Could not load page: {err}")
            return
        self.cache.put_content(page, markdown)
        if self.current and self.current.id == page.id:
            await self._render_page(page, markdown)
        self._prefetch_next()

    async def _fetch_content(self, page: Page) -> str:
        if page.kind == "data_source":
            return rows_markdown(*await ntn.query_rows(page.id))
        return await ntn.get_markdown(page.id)

    def _prefetch_next(self) -> None:
        """Warm the cache for the row below the cursor, so moving down feels instant."""
        index = self.query_one(PageList).index
        if index is None or index + 1 >= len(self.shown):
            return
        nxt = self.shown[index + 1]
        if not self.cache.is_fresh(nxt):
            self.prefetch(nxt)

    @work(exclusive=True, group="prefetch")
    async def prefetch(self, page: Page) -> None:
        try:
            self.cache.put_content(page, await self._fetch_content(page))
            await self._complete_path(page)
        except NtnError:
            pass  # best effort; the real fetch will report errors

    # ---------- actions ----------

    def action_focus_search(self) -> None:
        self.screen.remove_class("full")
        self.query_one("#search", Input).focus()

    def action_back(self) -> None:
        search = self.query_one("#search", Input)
        if search.has_focus and search.value:
            search.value = ""
            return
        self.screen.remove_class("full")
        self.query_one(PageList).focus()

    def action_refresh(self) -> None:
        self.next_cursor = None
        if self.current:
            self.cache.invalidate(self.current.id)
        self.load_list(self.query_text)

    def _is_page(self, page: Page | None, verb: str) -> bool:
        if page is not None and page.kind != "page":
            self.notify(f"Databases can't be {verb} here. Press o to open it in Notion.", severity="warning")
            return False
        return page is not None

    def action_edit(self) -> None:
        page = self.current
        if not self._is_page(page, "edited"):
            return
        with self.suspend():
            code = ntn.run_interactive("pages", "edit", page.id)
        if code != 0:
            self.notify(f"ntn pages edit exited with status {code}", severity="warning")
        # The list reload brings the new last_edited; the stale cached preview is then refetched.
        self.cache.invalidate(page.id)
        self.load_list(self.query_text)

    def action_new(self) -> None:
        with self.suspend():
            code = ntn.run_interactive("pages", "create")
        if code != 0:
            self.notify(f"ntn pages create exited with status {code}", severity="warning")
        self.load_list(self.query_text)

    def action_trash(self) -> None:
        page = self.current
        if not self._is_page(page, "trashed"):
            return

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.trash_page(page)

        self.push_screen(ConfirmScreen(f"Move “{page.title}” to the Notion trash?"), confirmed)

    @work(group="trash")
    async def trash_page(self, page: Page) -> None:
        try:
            await ntn.trash(page.id)
        except NtnError as err:
            self.notify(str(err), title="ntn trash failed", severity="error")
            return
        self.cache.forget(page.id)
        index = next((i for i, p in enumerate(self.shown) if p.id == page.id), 0)
        await self._show_pages([p for p in self.loaded if p.id != page.id], fallback_index=index)
        self.notify(f"Trashed “{page.title}”")

    def action_options(self) -> None:
        def closed(_: None) -> None:
            self.call_later(self._options_changed)

        self.push_screen(OptionsScreen(self.options), closed)

    async def _options_changed(self) -> None:
        await self._render_list(keep_id=self.current.id if self.current else None)
        if self._unchecked_entries():
            self.check_bodies()

    def action_browser(self) -> None:
        page = self.current
        if not (page and page.url):
            return
        desktop.open_url(page.url)
        self.raise_browser(page)

    @work(exclusive=True, group="raise")
    async def raise_browser(self, page: Page) -> None:
        """Bring the browser window showing the page to the front (GNOME Window Calls extension)."""
        await desktop.raise_page_window(page.title)

    def action_help(self) -> None:
        if self.screen.query("HelpPanel"):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()
