"""Layout A: page list on the left, Markdown preview on the right."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Input, Label, ListItem, ListView, Markdown, Static

from . import claude, desktop, editor, narrow, ntn, query, rows as row_edit
from .bidi import BidiMarkdown, BidiMixin
from .cache import MAX_DEPTH, Cache
from .database import (
    SKIPPED_VIEW_TYPES,
    DbRequest,
    DbRows,
    can_edit_rows,
    can_switch_layout,
    find_row,
    grid_columns,
    next_layout,
    people_names,
    replace_row,
    rows_markdown,
    sort_label,
    trash_question,
    view_columns,
    view_label,
)
from .grid import NEW_ROW, RowGrid
from .links import Link, notion_id, notion_links_to_markdown, page_links, same_file
from .modals import (
    CellPickScreen,
    CellTextScreen,
    ConfirmScreen,
    LinkPickScreen,
    NewPageScreen,
    OptionsScreen,
    PromptEditScreen,
    RepoPickScreen,
    SortScreen,
)
from .new_page import Location, NewPage, nearby_locations, page_path, with_unindexed
from .ntn import Node, NtnError, Page, View
from .options import Options

PREVIEW_DEBOUNCE = 0.12  # seconds to wait after the cursor stops before loading a preview
SEARCH_DEBOUNCE = 0.4  # seconds to wait after typing stops before asking Notion
LOAD_MORE_MARGIN = 5  # fetch the next batch when the cursor is this close to the end
BODY_CHECKS = 2  # parallel ntn calls when checking which database entries have content
VIEW_FETCHES = 2  # parallel ntn calls when fetching a database's views
SAVED_MARK_SECONDS = 1.5  # how long a grid row shows ✓ after a save
LEAVE_ALT_SCREEN = "\x1b[?1049l"  # terminal code that switches back to the shell's screen

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
    md = notion_links_to_markdown(md)
    return properties, md.strip()


def _local(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return None


def long_date(iso: str) -> str:
    dt = _local(iso)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "unknown"


class TitleLabel(BidiMixin, Label):
    """A page title in the list; RTL words reordered for kitty."""


class PageItem(ListItem):
    def __init__(self, page: Page) -> None:
        super().__init__()
        self.page = page

    def compose(self) -> ComposeResult:
        marker = "▦ " if self.page.kind == "data_source" else ""
        yield TitleLabel(marker + self.page.title, classes="title", markup=False)


class Breadcrumb(BidiMixin, Widget):
    """One line: "Notes / Quick Notes". When too long, the start is cut and replaced by "…"."""

    parts: reactive[tuple[str, ...]] = reactive(())

    def bidi_text(self) -> str:
        return ""  # the path reads left to right; only words inside an RTL title move

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
        # Only while a grid is showing; otherwise `l` falls through to the app's links key.
        Binding("right,l", "app.focus_grid", "go to grid", key_display="→"),
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




def with_cached_tail(fresh: list[Page], cached: list[Page]) -> list[Page]:
    """A first batch of recent pages, followed by the cached pages older than all of it."""
    if not fresh:
        return fresh
    ids = {p.id for p in fresh}
    oldest = min(p.last_edited for p in fresh)
    return fresh + [p for p in cached if p.id not in ids and p.last_edited < oldest]


def vanished(fresh: list[Page], cached: list[Page], complete: bool) -> list[Page]:
    """Cached pages a first batch of recent pages proves gone (trashed, deleted or unshared in Notion).

    The batch holds every page edited after its oldest one, so a cached page edited later that is missing
    from it is gone; with `complete` (no further batch) every missing page is. Pages edited in the same
    minute as the batch's oldest may still come in the next batch, so they are kept.
    """
    ids = {p.id for p in fresh}
    if complete:
        return [p for p in cached if p.id not in ids]
    if not fresh:
        return []
    oldest = min(p.last_edited for p in fresh)
    return [p for p in cached if p.id not in ids and p.last_edited > oldest]


def same_rows(a: list[Page], b: list[Page]) -> bool:
    """Both lists would draw the same rows: same pages, titles, kinds and edit times, in the same order."""
    def key(p: Page) -> tuple[str, str, str, str]:
        return (p.id, p.title, p.kind, p.last_edited)
    return len(a) == len(b) and all(key(x) == key(y) for x, y in zip(a, b))


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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
    #crumbline { height: 1; margin-bottom: 1; }
    #crumbs { width: 1fr; height: 1; padding: 0 1; color: $text; }
    #dbmode { width: auto; height: 1; padding: 0 1; margin-right: 1; display: none; background: $boost; color: $text-muted; text-style: bold; }
    #dbmode.edit { background: $accent; color: $background; }
    #grid { height: 1fr; margin: 0 1; display: none; background: transparent; }
    PreviewScroll { padding: 0 1; }
    Screen.full #list-pane { display: none; }
    PageList { background: transparent; scrollbar-size-vertical: 1; }
    PageItem { height: 1; padding: 0 1; }
    PageList > PageItem.-highlight { background: $block-cursor-blurred-background; }
    PageList:focus > PageItem.-highlight { background: $accent; color: $background; text-style: bold; }
    PageItem .title { width: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }
    #preview { background: transparent; }
    #dbtabs, #dbinfo, #dberror { height: 1; padding: 0 1; display: none; }
    #dbinfo { color: $text-muted; margin-bottom: 1; }
    #dberror { color: $error; margin-bottom: 1; }
    #dbquery { height: 1; border: none; padding: 0 1; margin: 0 1; background: $boost; display: none; }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "search"),
        Binding("w", "switch_pane", "switch pane"),
        Binding("v", "db_edit_mode", "view/edit rows"),
        Binding("t", "db_layout", "list/table"),
        Binding("e", "edit", "edit"),
        Binding("n", "new", "new"),
        Binding("x", "trash", "delete"),
        Binding("o", "browser", "open in Notion"),
        Binding("l", "links", "links"),
        Binding("backspace", "link_back", "back to linking page"),
        Binding("r", "refresh", "refresh"),
        Binding("comma", "options", "options"),
        # kitty takes this key in the `nui` window; listed here so `?` shows it. See narrow.py.
        Binding("ctrl+alt+w", "narrow", "narrow width (70%)", show=False, key_display="ctrl+alt+w"),
        Binding("left_square_bracket", "db_view(-1)", "prev view"),
        Binding("right_square_bracket", "db_view(1)", "next view"),
        Binding("f", "db_filter", "filter"),
        Binding("s", "db_sort", "sort"),
        Binding("F", "db_clear", "clear filter+sort"),
        Binding("m", "db_more", "more rows"),
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
        self._editing = False  # an edit is under way: a second `e` would save over it with stale text
        self._spin = 0  # frame of the loading spinner in the status line
        self._spinner: Timer | None = None
        self._rendered_id: str | None = None
        self._preview_timer: Timer | None = None
        self._search_timer: Timer | None = None
        self.db_rows: DbRows | None = None  # rows of the database being previewed
        self._row_writes = asyncio.Lock()  # row changes reach Notion one at a time, in the order made
        self._trashed: dict[str, list[tuple[dict, int]]] = {}  # data source → rows deleted here and where they were
        self._query_from_grid = False  # the filter line was opened from the grid: go back there
        self._layouts: dict[str, str] = {}  # view → layout picked with `t`; not saved, gone at quit
        self._meta_fresh: set[str] = set()  # data sources whose views and schema were fetched this session
        self.links: list[Link] = []  # links in the page shown in the preview
        self._link_history: list[Page] = []  # pages left by following a link, newest last
        self._people: dict[str, dict[str, str]] = {}  # data source → person name → user ID, seen in its rows
        self._created: Page | None = None  # page made here that Notion's search may not list yet
        self._created_during = ""  # the search that was active when it was made

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Input(placeholder="/ search pages1…", id="search")
            yield Static("", id="status")
        with Horizontal(id="body"):
            with Vertical(id="list-pane"):
                yield PageList(id="pages")
            with Vertical(id="preview-pane"):
                with Horizontal(id="crumbline"):
                    yield Breadcrumb(id="crumbs")
                    yield Static("", id="dbmode")
                yield Static("", id="dbtabs")
                yield Static("", id="dbinfo")
                yield Input(placeholder="filter rows: prop:a,b  prop~text  prop>=2024  -prop:x  prop:empty  words…", id="dbquery", select_on_focus=False)
                yield Static("", id="dberror", markup=False)
                with PreviewScroll():
                    yield BidiMarkdown("", id="preview", open_links=False)
                yield RowGrid(id="grid")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#list-pane").border_title = "Pages"
        self.query_one(PageList).focus()
        cached = self.cache.recent_pages()
        if cached:
            await self._show_pages(cached)
        self.load_list("")
        self.run_worker(asyncio.to_thread(desktop.prune_files), group="prune")

    # ---------- list ----------

    def _update_status(self) -> None:
        count = f"{len(self.shown)}{'+' if self.next_cursor else ''} pages"
        hidden = len(self.loaded) - len(self.shown)
        if hidden:
            count += f" · {hidden} entries hidden"
        label = f"“{self.query_text}” · {count}" if self.query_text else f"Recent · {count}"
        if self._loading:
            label = f"{SPINNER[self._spin % len(SPINNER)]} {label} · loading"
            if self._spinner is None:
                self._spinner = self.set_interval(0.1, self._spin_once)
        elif self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        self.query_one("#status", Static).update(label)

    def _spin_once(self) -> None:
        self._spin += 1
        self._update_status()

    def _visible(self, pages: list[Page]) -> list[Page]:
        """Apply the database entries option. A row just created here stays visible."""
        mode = self.options.database_entries
        if mode == "yes":
            return pages
        created = self._created.id if self._created else None
        if mode == "never":
            return [p for p in pages if not p.is_entry or p.id == created]
        bodies = self.cache.has_body([p.id for p in pages if p.is_entry])
        return [p for p in pages if not p.is_entry or bodies[p.id] or p.id == created]

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
        pages = self._visible(self.loaded)
        if pages and same_rows(pages, self.shown):
            # Fresh data that draws the same rows (often the cached list, confirmed): rebuilding would blink.
            self.shown = pages
            for item, page in zip(list_view.query(PageItem), pages):
                item.page = page
            self._update_status()
            return
        self.shown = pages
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
        try:
            with self._busy():
                pages, next_cursor = await ntn.search(query, cursor)
        except NtnError as err:
            self.notify(str(err), title="ntn search failed", severity="error")
            return
        if query != self.query_text:
            return  # the user typed something else meanwhile
        self.cache.upsert_pages(pages)
        if not more:
            pages, self._created = with_unindexed(pages, self._created, query, self._created_during)
        self.next_cursor = next_cursor
        if more:
            known = {p.id for p in self.loaded}
            await self._append_pages(pages)
            if next_cursor and all(p.id in known for p in pages):
                # Every page was already listed from the cache: no new row to scroll onto, so fetch on.
                self.load_list(query, more=True)
            return
        if not query:
            cached = self.cache.recent_pages()
            for gone in vanished(pages, cached, complete=not next_cursor):
                self.cache.forget(gone.id)  # so the next startup does not show it and then drop it
            if next_cursor:
                # Keep the cached pages older than this first batch, so the list does not shrink to one batch
                # at startup or on refresh. Later batches skip the ones already listed.
                pages = with_cached_tail(pages, cached)
        await self._show_pages(pages, keep_id=self.current.id if self.current else None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if not isinstance(event.item, PageItem):
            return
        self.current = event.item.page
        index = event.list_view.index or 0
        if self.next_cursor and not self._loading and index >= len(self.shown) - LOAD_MORE_MARGIN:
            self.load_list(self.query_text, more=True)
        self.refresh_bindings()  # database keys show only on databases
        if self._preview_timer:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(PREVIEW_DEBOUNCE, self._show_current)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.screen.add_class("full")
        self._focus_preview()

    # ---------- search ----------

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "dbquery":
            self._show_db_error("")
            return
        if event.input.id != "search":
            return
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
        if event.input.id == "dbquery":
            self._apply_db_query(event.value.strip())
            return
        self.query_one(PageList).focus()

    # ---------- preview ----------

    async def _render_message(self, text: str) -> None:
        self._rendered_id = None
        self.links = []
        self.refresh_bindings()
        self.query_one("#preview-pane").border_subtitle = ""
        self.query_one(Breadcrumb).parts = ()
        self._render_db_bar()
        await self.query_one("#preview", Markdown).update(f"_{text}_")

    async def _render_page(self, page: Page, markdown: str | None, note: str = "") -> None:
        self.query_one("#preview-pane").border_subtitle = note
        self._render_db_bar()
        if markdown is None:
            content = "_loading…_"
        else:
            properties, body = split_page(markdown, page.title)
            content = body or "_(empty page)_"
            if properties:
                content = "  \n".join(f"**{k}:** {v}" for k, v in properties) + "\n\n---\n\n" + content
        await self.query_one("#preview", Markdown).update(content)
        self.links = page_links(content) if markdown is not None else []
        self.refresh_bindings()
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
        # The cached preview is in the view's own layout: not worth a flash under a layout picked with `t`.
        await self._render_page(page, cached[0] if cached and not self._custom_layout(page) else None, note="refreshing…")
        self.fetch_preview(page)

    @work(exclusive=True, group="preview")
    async def fetch_preview(self, page: Page) -> None:
        try:
            markdown = await self._fetch_content(page)
        except NtnError as err:
            if self.current and self.current.id == page.id:
                if page.kind == "data_source":
                    # Keep the toolbar, so a bad view filter or sort can be changed.
                    self.db_rows = DbRows(page.id)
                    await self._render_page(page, f"_Could not load rows: {err}_")
                    if self._editing_rows():  # the grid has no place for the message
                        self.notify(str(err), title="Could not load rows", severity="error")
                    if page.id not in self._meta_fresh:  # the cached view may be what Notion refused
                        self.refresh_db_meta(page)
                else:
                    await self._render_message(f"Could not load page: {err}")
            return
        self._cache_preview(page, markdown)
        if self.current and self.current.id == page.id:
            await self._render_page(page, markdown)
            self.refresh_bindings()
        self._prefetch_next()
        if page.kind == "data_source" and page.id not in self._meta_fresh:
            self.refresh_db_meta(page)

    async def _fetch_content(self, page: Page) -> str:
        if page.kind == "data_source":
            return await self._fetch_db(page)
        return await ntn.get_markdown(page.id)

    # ---------- database views ----------

    def _db_request(self, data_source_id: str) -> DbRequest:
        """The current view, query line and sort override of a database (from the cache), as a query."""
        views = self.cache.views(data_source_id)
        schema = self.cache.schema(data_source_id)
        view_id, query_text, sorts_override = self.cache.db_state(data_source_id)
        view = next((v for v in views if v.id == view_id), views[0] if views else None)
        req = DbRequest(views if views or schema is not None else None, schema, view, query_text, sorts_override)
        req.filter = view.filter if view else None
        if query_text and schema is not None:
            try:
                req.filter = self._query_filter(data_source_id, query_text, schema, view)
            except query.QueryError as err:
                req.error = f"query not applied: {err}"
        req.sorts = sorts_override if sorts_override is not None else (view.sorts if view else None)
        req.columns = view_columns(view, schema)
        if can_switch_layout(view):
            req.layout = self._layouts.get(view.id, view.type)
            req.custom_layout = req.layout != view.type
        return req

    def _query_filter(self, data_source_id: str, text: str, schema: list[dict], view: View | None) -> dict | None:
        """The view's filter AND the query line, as one Notion filter. Raises QueryError."""
        filters = query.parse(text, schema, self._people.get(data_source_id))
        return query.combine(view.filter if view else None, filters)

    def _learn_people(self, data_source_id: str, rows: list[dict]) -> None:
        self._people.setdefault(data_source_id, {}).update(people_names(rows))

    def _current_rows(self) -> DbRows | None:
        """The loaded rows, when they belong to the database being previewed."""
        rows = self.db_rows
        return rows if rows and self.current and rows.page_id == self.current.id else None

    def _save_db_state(self, data_source_id: str, **changes) -> None:
        view_id, query_text, sorts = self.cache.db_state(data_source_id)
        state = {"view_id": view_id, "query_text": query_text, "sorts": sorts} | changes
        self.cache.put_db_state(data_source_id, state["view_id"], state["query_text"], state["sorts"])

    async def _fetch_meta(self, data_source_id: str) -> bool:
        """Fetch a database's views and schema into the cache. True when something changed."""
        limit = asyncio.Semaphore(VIEW_FETCHES)

        async def view(view_id: str) -> View:
            async with limit:
                return await ntn.get_view(view_id)

        async def views() -> list[View]:
            try:
                found = await asyncio.gather(*(view(i) for i in await ntn.list_view_ids(data_source_id)))
            except NtnError:
                # No access to views, or a passing error: keep what's cached (none = the plain table).
                return self.cache.views(data_source_id)
            return [v for v in found if v.type not in SKIPPED_VIEW_TYPES]

        schema, fetched = await asyncio.gather(ntn.get_schema(data_source_id), views())
        changed = False
        if schema != self.cache.schema(data_source_id):
            self.cache.put_schema(data_source_id, schema)
            changed = True
        if fetched != self.cache.views(data_source_id):
            self.cache.put_views(data_source_id, fetched)
            changed = True
        self._meta_fresh.add(data_source_id)
        return changed

    async def _fetch_db(self, page: Page) -> str:
        """Query the database's rows for its current view and query line; return its preview."""
        if self.cache.schema(page.id) is None:  # first time: views and schema decide what to ask for
            await self._fetch_meta(page.id)
        req = self._db_request(page.id)
        rows, cursor = await ntn.query_rows(page.id, req.filter, req.sorts)
        self._learn_people(page.id, rows)
        if req.error:  # a saved people term may parse now that these rows named the people
            retry = self._db_request(page.id)
            if not retry.error:
                req = retry
                rows, cursor = await ntn.query_rows(page.id, req.filter, req.sorts)
        if self.current and self.current.id == page.id:
            self.db_rows = DbRows(page.id, rows, cursor, req.columns, req.filter, req.sorts)
        return self._rows_preview(page.id, rows, cursor is not None, req.columns)

    def _rows_preview(self, data_source_id: str, rows: list[dict], has_more: bool, columns: list[str] | None) -> str:
        """The rows as Markdown, in the layout the view has now: `t` may have changed it during a fetch."""
        return rows_markdown(rows, has_more, columns, self._db_request(data_source_id).layout)

    def _custom_layout(self, page: Page) -> bool:
        return page.kind == "data_source" and self._db_request(page.id).custom_layout

    def _cache_preview(self, page: Page, markdown: str) -> None:
        """Keep the preview for instant display, unless it shows a layout picked with `t`: those aren't saved."""
        if not self._custom_layout(page):
            self.cache.put_content(page, markdown)

    @work(exclusive=True, group="dbmeta")
    async def refresh_db_meta(self, page: Page) -> None:
        """Refresh a database's views and schema; reload its rows when they changed."""
        try:
            changed = await self._fetch_meta(page.id)
        except NtnError as err:
            self._meta_fresh.add(page.id)  # don't retry on every cursor move
            self.notify(str(err), title="Could not load database views", severity="warning")
            return
        if self.current and self.current.id == page.id:
            if changed:
                self._reload_db()
            else:
                self._render_db_bar()

    def _is_db(self) -> bool:
        return self.current is not None and self.current.kind == "data_source"

    def _reload_db(self) -> None:
        """Requery the current database after its view, query or sort changed."""
        page = self.current
        if page is None:
            return
        if rows := self._current_rows():
            rows.cursor = None
            rows.reloading = True
        self.query_one("#preview-pane").border_subtitle = "refreshing…"
        self._render_db_bar(loading=True)
        self.fetch_preview(page)

    def _render_db_bar(self, loading: bool = False) -> None:
        """The database toolbar: view tabs, then query · sort · row count."""
        tabs, info = self.query_one("#dbtabs", Static), self.query_one("#dbinfo", Static)
        page = self.current
        if not self._is_db():
            for widget in (tabs, info, self.query_one("#dbmode"), self.query_one("#dbquery"), self.query_one("#dberror")):
                widget.display = False
            self._sync_grid()
            return
        req = self._db_request(page.id)
        tabs.display = info.display = True
        line = Text(no_wrap=True, overflow="ellipsis")
        if req.views is None:
            line.append("loading views…", style="dim")
        elif not req.views:
            line.append("no views", style="dim")
        for i, view in enumerate(req.views or []):
            if i:
                line.append("  ")
            label = f" {view_label(view)} "
            line.append(label, style="bold reverse" if req.view and view.id == req.view.id else "dim")
        tabs.update(line)

        parts = []
        if req.view and req.view.filter:
            parts.append("view filter")
        parts.append(f"filter: {req.query_text}" if req.query_text else "no filter")
        if req.sorts_override is not None:
            parts.append(f"sort: {sort_label(req.sorts, req.schema) or 'none'} (custom)")
        else:
            parts.append(f"sort: {sort_label(req.sorts, req.schema)}" if req.sorts else "unsorted")
        editing = self._editing_rows()
        if req.custom_layout and not editing:
            parts.append(f"layout: {req.layout} (custom)")
        rows = self._current_rows()
        if rows is None or loading:
            parts.append("loading…")
        else:
            parts.append(f"{len(rows.rows)}{'+' if rows.cursor else ''} row{'' if len(rows.rows) == 1 and not rows.cursor else 's'}")
        if editing and rows is not None and not loading and not rows.rows:
            parts.append("press n to add a row")
        info.update(Text(" · ".join(parts), no_wrap=True, overflow="ellipsis"))
        mode = self.query_one("#dbmode", Static)
        mode.update("EDIT" if editing else "VIEW")
        mode.set_class(editing, "edit")
        mode.display = True
        if not self.query_one("#dbquery").display:
            self._show_db_error(req.error)
        self._sync_grid()

    # ---------- database rows: Edit mode ----------

    def _editing_rows(self) -> bool:
        """The grid is what shows for the current database: Edit mode, on a view that allows it."""
        if not self._is_db() or not self.cache.db_edit_mode(self.current.id):
            return False
        req = self._db_request(self.current.id)
        return req.schema is not None and can_edit_rows(req.view)

    def _sync_grid(self) -> None:
        """Show the grid or the Markdown preview, whichever the current database calls for."""
        grid, preview = self.query_one(RowGrid), self.query_one(PreviewScroll)
        editing = self._editing_rows()
        if editing:
            req = self._db_request(self.current.id)
            loaded = self._current_rows()
            rows = loaded.rows if loaded else grid.source or []
            names = grid_columns(req.schema, req.columns)
            if grid.stale(rows, names):
                grid.show(rows, names)
        switched, focused = editing != grid.display, grid.has_focus or preview.has_focus
        grid.display, preview.display = editing, not editing
        if switched and focused:
            (grid if editing else preview).focus()  # the focus stays on this side
        if switched:
            self.refresh_bindings()  # the list's key into the grid comes and goes with it

    def _after_grid(self) -> Widget:
        """What takes the focus when the grid goes away."""
        return self.query_one(PreviewScroll if self.screen.has_class("full") else PageList)

    def _focus_preview(self) -> None:
        """Focus the preview side: the grid when it is showing, otherwise the Markdown preview."""
        grid = self.query_one(RowGrid)
        (grid if grid.display else self.query_one(PreviewScroll)).focus()

    def _focus_list(self) -> None:
        """Show the list pane again (leaving full width) and focus the page list."""
        self.screen.remove_class("full")
        self.query_one(PageList).focus()

    def action_focus_grid(self) -> None:
        self.query_one(RowGrid).focus()

    def action_db_edit_mode(self) -> None:
        if not self._is_db():
            return
        if self.query_one(RowGrid).has_focus:  # `v` in the grid: back to View
            self._set_edit_mode(False)
        else:
            self._go_to_grid()

    def _go_to_grid(self) -> None:
        """Focus the grid, switching the database to Edit mode first if it isn't showing."""
        grid = self.query_one(RowGrid)
        if grid.display:  # a grid in plain sight: "let me at it"
            grid.focus()
            return
        req = self._db_request(self.current.id)
        if req.schema is None:
            self.notify("The database's properties aren't loaded yet.", severity="warning")
        elif not can_edit_rows(req.view):
            self.notify(f"Edit mode works on list and table views. This one is a {req.view.type}.", severity="warning")
        else:
            self._set_edit_mode(True)

    def _set_edit_mode(self, on: bool) -> None:
        self.cache.put_db_edit_mode(self.current.id, on)
        self._render_db_bar()
        if on:
            self.query_one(RowGrid).focus()

    def _cursor_row(self) -> tuple[DbRows, dict] | None:
        """The loaded rows and the row under the grid's cursor."""
        rows = self._current_rows()
        row = find_row(rows.rows, self.query_one(RowGrid).current_row_id) if rows else None
        return (rows, row) if row else None

    def _cursor_cell(self) -> tuple[dict, str, dict] | None:
        """The row, column name and property under the grid's cursor."""
        found, name = self._cursor_row(), self.query_one(RowGrid).current_name
        if not found or name is None or name not in found[1]["properties"]:
            return None
        return found[1], name, found[1]["properties"][name]

    def on_data_table_cell_selected(self, event: RowGrid.CellSelected) -> None:
        cell = self._cursor_cell()
        if not cell:
            return
        row, name, prop = cell
        kind = prop.get("type", "")
        match row_edit.editor(kind):
            case "check":
                self._save_cell(row["id"], name, *row_edit.toggled(prop))
            case "text":
                self._edit_text_cell(row["id"], name, kind, prop)
            case "pick" | "multi" as how:
                self._edit_pick_cell(row["id"], name, kind, prop, how == "multi")
            case _:
                self.notify(f"Notion sets {name.strip()}; it can't be changed here.", severity="warning")

    def _edit_text_cell(self, row_id: str, name: str, kind: str, prop: dict) -> None:
        def done(change: tuple[dict, str] | None) -> None:
            if change:
                self._save_cell(row_id, name, *change)

        screen = CellTextScreen(row_edit.edit_text(prop), self.query_one(RowGrid).cell_region(), lambda text: row_edit.parse_text(kind, text))
        self.push_screen(screen, done)

    def _edit_pick_cell(self, row_id: str, name: str, kind: str, prop: dict, multi: bool) -> None:
        page = self.current
        people = self._people.get(page.id, {})
        schema = self.cache.schema(page.id) or []
        options = sorted(people) if kind == "people" else next((p["options"] for p in schema if p["name"] == name), [])
        chosen = row_edit.picked(prop)

        def done(names: list[str] | None) -> None:
            if names is None or names == chosen:
                return
            self._remember_options(page.id, name, names)
            self._save_cell(row_id, name, *row_edit.pick_value(kind, names, people))

        cell = self.query_one(RowGrid).cell_region()
        self.push_screen(CellPickScreen(options, chosen, cell, multi, row_edit.can_add_options(kind)), done)

    def _remember_options(self, data_source_id: str, name: str, names: list[str]) -> None:
        """Keep options added here in the cached schema, so the pick list and the filter line know them."""
        schema = self.cache.schema(data_source_id) or []
        prop = next((p for p in schema if p["name"] == name), None)
        new = [n for n in names if prop and prop["type"] != "people" and n not in prop["options"]]
        if new:
            prop["options"] += new
            self.cache.put_schema(data_source_id, schema)

    def _save_cell(self, row_id: str, name: str, value: dict, shown: str) -> None:
        grid = self.query_one(RowGrid)
        grid.set_cell(row_id, name, shown)
        grid.mark(row_id, "…")
        self._write_cell(self.current, row_id, name, value)

    @work(group="row-write")
    async def _write_cell(self, page: Page, row_id: str, name: str, value: dict) -> None:
        grid = self.query_one(RowGrid)
        rows = self._rows_of(page)
        try:
            async with self._row_writes:
                with self._busy():
                    fresh = await ntn.update_row(row_id, {name: value})
        except NtnError as err:
            self.notify(str(err), title=f"Could not save {name.strip()}", severity="error")
            old = find_row(rows.rows, row_id) if rows else None
            if old and self._rows_of(page) is rows:
                grid.redraw_row(old)
                grid.mark(row_id, " ")
            return
        rows = self._rows_of(page)
        if rows is None:
            self.cache.invalidate(page.id)  # moved on: the cached table is out of date
            return
        replace_row(rows.rows, fresh)
        grid.redraw_row(fresh)
        grid.mark(row_id, "✓")
        self.set_timer(SAVED_MARK_SECONDS, lambda: grid.mark(row_id, " "))
        await self._store_rows(page, rows)

    def _rows_of(self, page: Page) -> DbRows | None:
        rows = self._current_rows()
        return rows if rows and rows.page_id == page.id else None

    async def _store_rows(self, page: Page, rows: DbRows) -> None:
        """After rows or their layout changed: refresh the Markdown preview View mode shows, and the cached copy."""
        markdown = self._rows_preview(page.id, rows.rows, rows.cursor is not None, rows.columns)
        self._cache_preview(page, markdown)
        await self._render_page(page, markdown)

    def action_row_new(self) -> None:
        grid, page = self.query_one(RowGrid), self.current
        schema = self.cache.schema(page.id) or []
        title = next((p["name"] for p in schema if p["type"] == "title"), None)
        if title not in grid.names or self._current_rows() is None:
            self.notify("The rows aren't loaded yet.", severity="warning")
        elif grid.has_row(NEW_ROW):
            self.notify("Still adding the last row.", severity="warning")
        else:
            grid.add_new_row(title)
            self.call_after_refresh(self._ask_new_title, page, title)

    def _ask_new_title(self, page: Page, title: str) -> None:
        grid = self.query_one(RowGrid)

        def done(text: str | None) -> None:
            if text:
                grid.set_cell(NEW_ROW, title, text)
                grid.mark(NEW_ROW, "…")
                self._create_row(page, text)
            else:
                grid.drop_row(NEW_ROW)

        self.push_screen(CellTextScreen("", grid.cell_region(), str.strip), done)

    @work(group="row-write")
    async def _create_row(self, page: Page, title: str) -> None:
        grid = self.query_one(RowGrid)
        try:
            async with self._row_writes:
                with self._busy():
                    row = await ntn.create_row(page.id, title)
        except NtnError as err:
            grid.drop_row(NEW_ROW)
            self.notify(str(err), title="Could not add the row", severity="error")
            return
        rows = self._rows_of(page)
        if rows is None:
            self.cache.invalidate(page.id)
            return
        grid.drop_row(NEW_ROW)
        rows.rows.append(row)
        self._learn_people(page.id, [row])
        await self._store_rows(page, rows)
        grid.select_row(row["id"])

    async def action_row_trash(self) -> None:
        found = self._cursor_row()
        if not found:
            return
        rows, row = found
        page, index = self.current, rows.rows.index(row)
        entry = (row, index)
        rows.rows.remove(row)
        self.query_one(RowGrid).drop_row(row["id"])
        self._trashed.setdefault(page.id, []).append(entry)
        self._trash_row(page, row, entry)
        self.notify(f"Trashed “{ntn.row_title(row)}”. Press u to bring it back.")
        await self._store_rows(page, rows)

    @work(group="row-write")
    async def _trash_row(self, page: Page, row: dict, entry: tuple[dict, int]) -> None:
        try:
            async with self._row_writes:
                with self._busy():
                    await ntn.trash(row["id"])
        except NtnError as err:
            self.notify(str(err), title="Could not delete the row", severity="error")
            stack = self._trashed.get(page.id, [])
            if entry in stack:
                stack.remove(entry)
                await self._put_row_back(page, row, entry[1])
            return
        if any(p.id == row["id"] for p in self.loaded):  # the page list shows entries too
            await self._show_pages([p for p in self.loaded if p.id != row["id"]], keep_id=self.current.id if self.current else None)
        self.cache.forget(row["id"])

    async def _put_row_back(self, page: Page, row: dict, index: int) -> None:
        rows = self._rows_of(page)
        if rows is None:
            self.cache.invalidate(page.id)
            return
        rows.rows.insert(min(index, len(rows.rows)), row)
        await self._store_rows(page, rows)
        self.query_one(RowGrid).select_row(row["id"])

    async def action_row_restore(self) -> None:
        page = self.current
        stack = self._trashed.get(page.id)
        if not stack:
            self.notify("Nothing to bring back.", severity="warning")
            return
        row, index = stack.pop()
        await self._put_row_back(page, row, index)
        self._restore_row(page, row)

    @work(group="row-write")
    async def _restore_row(self, page: Page, row: dict) -> None:
        try:
            async with self._row_writes:
                with self._busy():
                    await ntn.restore(row["id"])
        except NtnError as err:
            self.notify(str(err), title="Could not bring the row back", severity="error")
            rows = self._rows_of(page)
            if rows and row in rows.rows:
                rows.rows.remove(row)
                await self._store_rows(page, rows)
            return
        self.notify(f"Restored “{ntn.row_title(row)}”.")

    def action_row_body(self) -> None:
        found = self._cursor_row()
        if found:
            self._edit_in_editor(ntn.row_page(found[1]))

    def action_row_claude(self) -> None:
        found, name = self._cursor_row(), self.query_one(RowGrid).current_name
        prop = found[1]["properties"].get(name) if found and name else None
        text = ntn.value_text(prop).strip() if prop else ""  # in full: the grid cuts long cells
        if not text:
            self.notify("The cell is empty: no task to give Claude.", severity="warning")
            return

        def picked(choice: str | None) -> None:
            if not choice:
                return
            repo = next((r for r in claude.REPOS if r.label == choice), None) or claude.Repo(
                choice, claude.repo_path(choice))

            def edited(prompt: str | None) -> None:
                if prompt:
                    self._start_claude(repo, text, prompt)

            self.push_screen(PromptEditScreen(claude.task_prompt(text), repo.label), edited)

        self.push_screen(RepoPickScreen([r.label for r in claude.REPOS]), picked)

    @work(group="claude")
    async def _start_claude(self, repo: claude.Repo, text: str, prompt: str) -> None:
        self.notify(f"Starting Claude in {repo.label}…")
        try:
            self.notify(await claude.start_session(repo, text, prompt, lambda message: self.notify(message, timeout=15)))
        except (OSError, RuntimeError, KeyError) as error:
            self.notify(str(error), title="Claude session not started", severity="error", timeout=10)

    def action_row_copy(self) -> None:
        """Copy the full value of the cell under the grid's cursor to the clipboard."""
        cell = self._cursor_cell()
        if not cell:
            return
        row, name, prop = cell
        if self.query_one(RowGrid).saving(row["id"]):
            self.notify("Still saving the row; copy again when it's saved.", severity="warning")
            return
        text = row_edit.copy_text(prop)
        if not text:
            self.notify("The cell is empty.", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify(f"Copied {name.strip()}.", timeout=2)

    def _show_db_error(self, message: str) -> None:
        error = self.query_one("#dberror", Static)
        error.update(message)
        error.display = bool(message)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("db_"):
            if not self._is_db():
                return False
            if action == "db_more":
                rows = self._current_rows()
                return bool(rows and rows.cursor)
            if action == "db_layout":
                return can_switch_layout(self._db_request(self.current.id).view) and not self._editing_rows()
        if action == "focus_grid":
            return self._is_db() and self.query_one(RowGrid).display
        if action == "links":
            return bool(self.links)
        if action == "link_back":
            return bool(self._link_history)
        return True

    def action_db_view(self, step: int) -> None:
        if not self._is_db():
            return
        req = self._db_request(self.current.id)
        if not req.views:
            self.notify("This database has no views to switch between.", severity="warning")
            return
        index = req.views.index(req.view) if req.view in req.views else 0
        self._save_db_state(self.current.id, view_id=req.views[(index + step) % len(req.views)].id)
        self._reload_db()

    async def action_db_layout(self) -> None:
        """Lay out the current view's rows the other way, from the rows already loaded."""
        req = self._db_request(self.current.id)
        self._layouts[req.view.id] = next_layout(req.layout)
        rows = self._current_rows()
        if rows and rows.rows and not rows.reloading:
            await self._store_rows(self.current, rows)
        else:  # no rows yet, a load error to keep on screen, or a reload that will draw in the new layout
            self._render_db_bar(loading=bool(rows and rows.reloading))

    def action_db_filter(self) -> None:
        if not self._is_db():
            return
        box = self.query_one("#dbquery", Input)
        self._query_from_grid = self.query_one(RowGrid).has_focus
        box.value = self.cache.db_state(self.current.id)[1]
        box.cursor_position = len(box.value)  # refine the current filter: type on at the end
        box.display = True
        box.focus()

    def _close_db_query(self) -> None:
        box = self.query_one("#dbquery", Input)
        box.display = False
        self._show_db_error("")
        grid = self.query_one(RowGrid)
        (grid if self._query_from_grid and grid.display else self._after_grid()).focus()

    def _apply_db_query(self, text: str) -> None:
        page = self.current
        if not self._is_db():
            self._close_db_query()
            return
        req = self._db_request(page.id)
        if text and req.schema is None:
            self._show_db_error("The database's properties aren't loaded yet; try again in a moment.")
            return
        try:
            self._query_filter(page.id, text, req.schema or [], req.view)
        except query.QueryError as err:
            self._show_db_error(str(err))
            return
        self._close_db_query()
        self._save_db_state(page.id, query_text=text)
        self._reload_db()

    def action_db_sort(self) -> None:
        if not self._is_db():
            return
        page = self.current
        req = self._db_request(page.id)
        if req.schema is None:
            self.notify("The database's properties aren't loaded yet.", severity="warning")
            return

        def chosen(sorts: list | None) -> None:
            if sorts is not None and self.current and self.current.id == page.id:
                self._save_db_state(page.id, sorts=sorts)
                self._reload_db()

        self.push_screen(SortScreen(req.sorts or [], req.schema), chosen)

    def action_db_clear(self) -> None:
        if not self._is_db():
            return
        if self.query_one("#dbquery").display:
            self._close_db_query()
        self._save_db_state(self.current.id, query_text="", sorts=None)
        self._reload_db()

    def action_db_more(self) -> None:
        rows = self._current_rows()
        if rows and rows.cursor:
            self.load_more_rows(self.current)

    @work(exclusive=True, group="preview")
    async def load_more_rows(self, page: Page) -> None:
        """Fetch the next 100 rows of the database being previewed and add them to the table."""
        rows = self.db_rows
        if not (rows and rows.page_id == page.id and rows.cursor):
            return
        self.query_one("#preview-pane").border_subtitle = "loading more rows…"
        try:
            more, cursor = await ntn.query_rows(page.id, rows.filter, rows.sorts, start_cursor=rows.cursor)
        except NtnError as err:
            self.query_one("#preview-pane").border_subtitle = ""
            self.notify(str(err), title="Could not load more rows", severity="error")
            return
        if not (self.current and self.current.id == page.id and self.db_rows is rows):
            return
        self._learn_people(page.id, more)
        rows.rows += more
        rows.cursor = cursor
        await self._store_rows(page, rows)
        self.refresh_bindings()

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
        if page.kind == "data_source" and self.cache.schema(page.id) is None:
            return  # a first look at a database costs a call per view: leave it to the real fetch
        try:
            self._cache_preview(page, await self._fetch_content(page))
            await self._complete_path(page)
        except NtnError:
            pass  # best effort; the real fetch will report errors

    # ---------- actions ----------

    def action_focus_search(self) -> None:
        self.screen.remove_class("full")
        self.query_one("#search", Input).focus()

    def action_switch_pane(self) -> None:
        """Move the focus between the page list and the preview, never to the search box (unlike tab)."""
        if self.query_one(PageList).has_focus:
            self._focus_preview()
        else:
            self._focus_list()

    def action_back(self) -> None:
        if self.query_one("#dbquery", Input).has_focus:
            self._close_db_query()  # cancel: the query line keeps its applied value
            return
        if self.query_one(RowGrid).has_focus:
            self._set_edit_mode(False)
            self._after_grid().focus()
            return
        search = self.query_one("#search", Input)
        if search.has_focus and search.value:
            search.value = ""
            return
        self._focus_list()

    def action_refresh(self) -> None:
        self.next_cursor = None
        if self.current:
            self.cache.invalidate(self.current.id)
            self._meta_fresh.discard(self.current.id)  # a database's views and schema are fetched again too
            # A list that comes back the same moves no cursor, so nothing else would refetch the preview or rows.
            # The shown preview stays, marked, until the fresh one replaces it.
            self.query_one("#preview-pane").border_subtitle = "refreshing…"
            if self._is_db():
                self._render_db_bar(loading=True)
            self.fetch_preview(self.current)
        self.load_list(self.query_text)

    def _is_page(self, page: Page | None, verb: str) -> bool:
        if page is not None and page.kind != "page":
            self.notify(f"Databases can't be {verb} here. Press o to open it in Notion.", severity="warning")
            return False
        return page is not None

    def action_edit(self) -> None:
        if self._is_db():  # a database's rows are edited in the grid
            self._go_to_grid()
            return
        page = self.current
        if self._is_page(page, "edited"):
            self._edit_in_editor(page)

    @work(group="edit")
    async def _edit_in_editor(self, page: Page) -> None:
        """Edit the page body in $EDITOR, then save it and reload the list.

        Notion is read before and written after with the TUI on screen; only the editor runs
        while it is suspended, so going to the editor and back doesn't flash the shell.
        """
        if self._editing:
            self.notify("Already editing a page", severity="warning")
            return
        self._editing = True
        try:
            await self._edit_body(page)
        finally:
            self._editing = False

    async def _edit_body(self, page: Page) -> None:
        try:
            with self._busy():
                body, truncated = await ntn.get_body(page.id)
        except NtnError as err:
            self.notify(str(err), title="Could not load the page", severity="error")
            return
        if truncated:  # too long to round-trip safely: let ntn handle it end to end
            with self.suspend():
                code = ntn.run_interactive("pages", "edit", page.id)
            if code != 0:
                self.notify(f"ntn pages edit exited with status {code}", severity="warning")
            self._reload_after_edit(page)
            return
        original = body + "\n"  # the same text `ntn pages edit` puts in the editor
        failed: OSError | None = None
        with self._suspend_on_screen():
            try:  # an exception here would leave the TUI suspended
                code, edited = editor.edit_text(original)
            except OSError as err:
                failed = err
        if failed is not None:
            self.notify(str(failed), title="Could not start the editor", severity="error")
            return
        if code != 0:
            self.notify(f"The editor exited with status {code}; nothing saved", severity="warning")
            return
        if edited == original:
            return
        if not edited.strip():
            self.notify("The page is empty in the editor; nothing saved", severity="warning")
            return
        try:
            with self._busy():
                await ntn.set_body(page.id, edited)
        except NtnError as err:
            kept = editor.keep_unsaved(page.id, edited)
            self.notify(f"{err}\nYour text is in {kept}", title="Could not save the page", severity="error")
            return
        self._reload_after_edit(page)

    @contextmanager
    def _busy(self) -> Iterator[None]:
        """Show the loading spinner while a Notion call runs."""
        self._loading += 1
        self._update_status()
        try:
            yield
        finally:
            self._loading -= 1
            self._update_status()

    def _reload_after_edit(self, page: Page) -> None:
        # The list reload brings the new last_edited; the stale cached preview is then refetched.
        self.cache.invalidate(page.id)
        self.load_list(self.query_text)

    @contextmanager
    def _suspend_on_screen(self) -> Iterator[None]:
        """`suspend()` that leaves the TUI's last frame on screen instead of switching back to the
        shell, so the editor opens straight over it."""
        driver = self._driver
        if driver is None:
            yield
            return
        write = driver.write
        driver.write = lambda data: write(data.replace(LEAVE_ALT_SCREEN, ""))
        try:
            with self.suspend():
                del driver.write  # only the leaving writes are filtered
                yield
        finally:
            driver.__dict__.pop("write", None)

    def _path_of(self, page: Page) -> str:
        return page_path(page, self.cache.ancestry(page.id))

    async def _find_parents(self, query: str) -> list[Location]:
        """Pages and databases matching `query`, as places for a new page."""
        pages, _ = await ntn.search(query, page_size=100)
        # Database rows can hold pages too, but are rarely meant as a parent.
        return [Location.inside(p, self._path_of(p)) for p in pages if not p.is_entry]

    def action_new(self) -> None:
        def chosen(new: NewPage | None) -> None:
            if new:
                self.create_page(new)

        ancestry = self.cache.ancestry(self.current.id) if self.current else ([], None)
        nearby = nearby_locations(self.current, ancestry)  # the top level is always offered
        self.push_screen(NewPageScreen(nearby, self._find_parents), chosen)

    @work(group="create")
    async def create_page(self, new: NewPage) -> None:
        """Create the titled page in its place, then open it in $EDITOR for the body."""
        loc = new.location
        try:
            page = await ntn.create_page(new.title, loc.parent_kind, loc.parent_id)
        except NtnError as err:
            self.notify(str(err), title="Could not create the page", severity="error")
            return
        self.cache.upsert_pages([page])
        self.cache.put_nodes([Node(page.id, "page", page.title, page.parent_kind, page.parent_id)])
        self._created, self._created_during = page, self.query_text
        await self._go_to(page)
        self._edit_in_editor(page)

    def action_trash(self) -> None:
        page = self.current
        if page is None:
            return
        if page.kind == "data_source":
            self.ask_trash_database(page)
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
        await self._drop_trashed(page, {page.id})

    @work(exclusive=True, group="trash-lookup")
    async def ask_trash_database(self, page: Page) -> None:
        """Find the database behind the listed data source, then confirm: it may hold more than this table."""
        try:
            database = await ntn.database_of(page.id)
        except NtnError as err:
            self.notify(str(err), title="Could not look up the database", severity="error")
            return

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.trash_database(page, database)

        self.push_screen(ConfirmScreen(trash_question(page.title, len(database.data_source_ids))), confirmed)

    @work(group="trash")
    async def trash_database(self, page: Page, database: ntn.Database) -> None:
        try:
            await ntn.trash_database(database.id)
        except NtnError as err:
            self.notify(str(err), title="Could not trash the database", severity="error")
            return
        self.cache.forget_database(database.ids)
        await self._drop_trashed(page, database.ids)

    async def _drop_trashed(self, page: Page, ids: set[str]) -> None:
        """Take a trashed page or database, and any rows inside it, off the list."""
        def gone(p: Page) -> bool:
            return p.id in ids or p.parent_id in ids

        if self._created and gone(self._created):
            self._created = None
        index = next((i for i, p in enumerate(self.shown) if p.id == page.id), 0)
        await self._show_pages([p for p in self.loaded if not gone(p)], fallback_index=index)
        self.notify(f"Trashed “{page.title}”")

    def action_options(self) -> None:
        def closed(_: None) -> None:
            self.call_later(self._options_changed)

        self.push_screen(OptionsScreen(self.options), closed)

    def action_narrow(self) -> None:
        if not (narrow.available() and narrow.set_on(not narrow.is_on())):
            self.notify("Narrow width works only in the window `nui` opens", severity="warning")

    async def _options_changed(self) -> None:
        await self._render_list(keep_id=self.current.id if self.current else None)
        if self._unchecked_entries():
            self.check_bodies()

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        self.follow_link(Link(event.href, event.href))

    # ---------- links ----------

    def action_links(self) -> None:
        if not self.links:
            return

        def chosen(link: Link | None) -> None:
            if link:
                self.follow_link(link)

        self.push_screen(LinkPickScreen(self.links), chosen)

    @work(exclusive=True, group="link")
    async def follow_link(self, link: Link) -> None:
        """Show a linked Notion page here; open an image locally; open any other link in the browser."""
        if link.image:
            await self._open_image(link)
            return
        page = await self._linked_page(link)
        if page is None:
            await desktop.open_and_raise(link.url)
            return
        if self.current and self.current.id != page.id:
            self._link_history.append(self.current)
        await self._go_to(page)

    async def _open_image(self, link: Link) -> None:
        """Download and open the image. Notion's file links expire after an hour, so when the
        cached link fails the page is fetched again for a fresh one."""
        try:
            await desktop.open_file(link.url)
            return
        except OSError:
            pass
        page = self.current
        if page is None or page.kind != "page":
            self.notify("Could not download the image", severity="error")
            return
        self.notify("Image link expired, refreshing the page…")
        try:
            markdown = await ntn.get_markdown(page.id)
        except NtnError as err:
            self.notify(f"Could not refresh the page: {err}", severity="error")
            return
        self.cache.put_content(page, markdown)
        if self.current and self.current.id == page.id:
            await self._render_page(page, markdown)
        fresh = next((l for l in page_links(split_page(markdown, page.title)[1]) if l.image and same_file(l.url, link.url)), None)
        try:
            if fresh is None:
                raise OSError("image no longer in the page")
            await desktop.open_file(fresh.url)
        except OSError as err:
            self.notify(f"Could not download the image: {err}", severity="error")

    async def _linked_page(self, link: Link) -> Page | None:
        hex_id = notion_id(link.url)
        if hex_id is None:
            return None
        page = self.cache.find_page(hex_id)
        if page:
            return page
        page_id = f"{hex_id[:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:]}"
        try:
            node = await ntn.get_node("page", page_id)
        except NtnError:
            return None  # a database not listed yet, or no access: Notion in the browser can show it
        self.cache.put_nodes([node])
        # last_edited unknown: the preview always fetches the content fresh.
        return Page(page_id, node.title or link.title, "", link.url, node.parent_kind, node.parent_id)

    async def _go_to(self, page: Page) -> None:
        """Select the page in the list, adding it at the top when it isn't there."""
        list_view = self.query_one(PageList)
        ids = [p.id for p in self.shown]
        if page.id not in ids:
            self.loaded.insert(0, page)
            self.shown.insert(0, page)
            await list_view.insert(0, [PageItem(page)])
            ids.insert(0, page.id)
            list_view.index = None  # the cursor may already be on row 0: make the move below register
        list_view.index = ids.index(page.id)
        self.refresh_bindings()

    def action_link_back(self) -> None:
        if self._link_history:
            self.call_later(self._go_to, self._link_history.pop())

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
