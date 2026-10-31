"""Layout A: page list on the left, Markdown preview on the right."""

from __future__ import annotations

import re
import subprocess
import webbrowser
from datetime import datetime

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Markdown, Static

from . import ntn
from .cache import Cache
from .ntn import NtnError, Page

PREVIEW_DEBOUNCE = 0.12  # seconds to wait after the cursor stops before loading a preview
SEARCH_DEBOUNCE = 0.4  # seconds to wait after typing stops before asking Notion
LOAD_MORE_MARGIN = 5  # fetch the next batch when the cursor is this close to the end

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


def _local(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return None


def short_date(iso: str) -> str:
    dt = _local(iso)
    if dt is None:
        return ""
    return dt.strftime("%m-%d") if dt.year == datetime.now().year else dt.strftime("%y-%m-%d")


def long_date(iso: str) -> str:
    dt = _local(iso)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "unknown"


class PageItem(ListItem):
    def __init__(self, page: Page) -> None:
        super().__init__()
        self.page = page

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label(self.page.title, classes="title", markup=False)
            yield Label(short_date(self.page.last_edited), classes="date")


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
    #preview-pane { width: 1fr; border: round $border-blurred; padding: 0 1; border-subtitle-color: $text-muted; }
    #preview-pane:focus { border: round $accent; }
    Screen.full #list-pane { display: none; }
    PageList { background: transparent; scrollbar-size-vertical: 1; }
    PageItem { height: 1; padding: 0 1; }
    PageList > PageItem.-highlight { background: $block-cursor-blurred-background; }
    PageList:focus > PageItem.-highlight { background: $accent; color: $background; text-style: bold; }
    PageList:focus > PageItem.-highlight .date { color: $background; }
    PageItem Horizontal { height: 1; }
    PageItem .title { width: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }
    PageItem .date { width: 9; text-align: right; color: $text-muted; }
    #preview { background: transparent; }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "search"),
        Binding("e", "edit", "edit"),
        Binding("n", "new", "new"),
        Binding("d", "trash", "trash"),
        Binding("o", "browser", "browser"),
        Binding("r", "refresh", "refresh"),
        Binding("escape", "back", "back", show=False),
        Binding("question_mark", "help", "help"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, cache: Cache | None = None) -> None:
        super().__init__()
        self.cache = cache or Cache()
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
            with PreviewScroll(id="preview-pane"):
                yield Markdown("", id="preview")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#list-pane").border_title = "Pages"
        self.query_one("#preview-pane").border_title = "Preview"
        self.query_one(PageList).focus()
        cached = self.cache.recent_pages()
        if cached:
            await self._show_pages(cached)
        self.load_list("")

    # ---------- list ----------

    def _update_status(self) -> None:
        count = f"{len(self.shown)}{'+' if self.next_cursor else ''} pages"
        label = f"“{self.query_text}” · {count}" if self.query_text else f"Recent · {count}"
        if self._loading:
            label += " · loading…"
        self.query_one("#status", Static).update(label)

    async def _show_pages(self, pages: list[Page], keep_id: str | None = None, fallback_index: int = 0) -> None:
        """Replace the list, keeping the cursor on `keep_id` when it is still present."""
        list_view = self.query_one(PageList)
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
        known = {p.id for p in self.shown}
        new = [p for p in pages if p.id not in known]
        self.shown.extend(new)
        await self.query_one(PageList).extend(PageItem(p) for p in new)
        self._update_status()

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
        await self.query_one("#preview", Markdown).update(f"_{text}_")

    async def _render_page(self, page: Page, markdown: str | None, note: str = "") -> None:
        pane = self.query_one("#preview-pane", PreviewScroll)
        pane.border_subtitle = note
        header = f"# {page.title}\n\n_edited {long_date(page.last_edited)}_"
        if page.url:
            header += f" · [open in Notion]({page.url})"
        if markdown is None:
            body = "_loading…_"
        else:
            properties, body = split_page(markdown, page.title)
            if properties:
                header += "\n\n" + "  \n".join(f"**{k}:** {v}" for k, v in properties)
            body = body or "_(empty page)_"
        await self.query_one("#preview", Markdown).update(f"{header}\n\n---\n\n{body}")
        if self._rendered_id != page.id:
            pane.scroll_home(animate=False)
            self._rendered_id = page.id

    async def _show_current(self) -> None:
        page = self.current
        if page is None:
            return
        cached = self.cache.content(page.id)
        if cached and cached[1] == page.last_edited:
            await self._render_page(page, cached[0])
            self._prefetch_next()
            return
        await self._render_page(page, cached[0] if cached else None, note="refreshing…")
        self.fetch_preview(page)

    @work(exclusive=True, group="preview")
    async def fetch_preview(self, page: Page) -> None:
        try:
            markdown = await ntn.get_markdown(page.id)
        except NtnError as err:
            if self.current and self.current.id == page.id:
                await self._render_message(f"Could not load page: {err}")
            return
        self.cache.put_content(page, markdown)
        if self.current and self.current.id == page.id:
            await self._render_page(page, markdown)
        self._prefetch_next()

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
            self.cache.put_content(page, await ntn.get_markdown(page.id))
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

    def action_edit(self) -> None:
        page = self.current
        if page is None:
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
        if page is None:
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
        await self._show_pages([p for p in self.shown if p.id != page.id], fallback_index=index)
        self.notify(f"Trashed “{page.title}”")

    def action_browser(self) -> None:
        if not (self.current and self.current.url):
            return
        try:
            subprocess.Popen(
                ["xdg-open", self.current.url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            webbrowser.open(self.current.url)

    def action_help(self) -> None:
        if self.screen.query("HelpPanel"):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()
