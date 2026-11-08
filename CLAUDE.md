# notion-tui

This project is called **notion-tui**, or **ntn-tui** in short.

It's a terminal UI for Notion (Python 3.13, Textual, run with `uv run notion-tui`): page list left, Markdown preview right, edit in `$EDITOR`.

- `src/notion_tui/app.py` — Textual app: list, preview, search, database view tabs / filter line / paging, View / Edit mode and row writes, key bindings.
- `src/notion_tui/database.py` — pure database-preview helpers: rows table and list layouts, view and grid columns, labels, sort labels. Tested in `tests/test_database.py`.
- `src/notion_tui/grid.py` — the Edit mode grid: rows and columns with a cell cursor, row keys, saving marks. Driven end to end in `tests/test_row_editing.py`.
- `src/notion_tui/rows.py` — pure row editing helpers: which property types are editable and how, typed text ↔ Notion property values. Tested in `tests/test_rows.py`.
- `src/notion_tui/modals.py` — modal dialogs: trash confirmation, options, new page (title + tree location picker), database sort picker, link picker, cell editors of the grid (text input, pick list).
- `src/notion_tui/new_page.py` — pure new-page helpers: where a new page can go (inside / next to the highlighted page, top level), keeping a new page listed until Notion's search finds it. Tested in `tests/test_new_page.py`.
- `src/notion_tui/links.py` — pure link helpers: Notion `<page>`/`<database>` tags → Markdown links, a page's links and images, Notion ID from a URL. Tested in `tests/test_links.py`.
- `src/notion_tui/ntn.py` — async wrapper over the `ntn` CLI; the only path to Notion (search, data-source schema and query, views (read-only), pages get/edit/trash/restore, database trash, page create with title and parent, row create and property update).
- `src/notion_tui/cache.py` — SQLite cache `~/.cache/notion-tui/cache.db` (`pages`, `page_content`, `nodes`; for databases `views`, `data_source_schema`, `db_state` including View / Edit mode) for instant startup and previews.
- `src/notion_tui/query.py` — pure parser: database filter line → Notion filter JSON, using the property schema. Tested in `tests/test_query.py` (`uv run pytest`).
- `src/notion_tui/editor.py` — runs `$VISUAL` / `$EDITOR` on a temp file (the app fetches and saves the page around it, so the TUI is hidden only while the editor runs); keeps text Notion refused in `~/.cache/notion-tui/unsaved`.
- `src/notion_tui/claude.py` — ctrl+o in Edit mode: Claude Code session on the cell's text (`task: <cell>`) in herdr: a new worktree of the picked repo via `herdr worktree create` (or a free Obics mr clone in a new space), Claude started with `herdr agent start` / `prompt`. Tested in `tests/test_claude.py`.
- `src/notion_tui/options.py` — user options in `~/.config/notion-tui/options.json`.
- `src/notion_tui/narrow.py` — narrow width setting: reads the flag file and asks kitty's watcher (my-automations) to change it over the `nui` window's control socket. Tested in `tests/test_narrow.py`.
- `src/notion_tui/desktop.py` — opens a page in the browser, raises the window via GNOME "Window Calls" D-Bus extension (also herdr's kitty window); downloads page images to `~/.cache/notion-tui/files` and opens them with `x`; files unopened for 7 days are deleted at startup.
- `src/notion_tui/bidi.py` — right-to-left text (Hebrew, Arabic): kitty draws letters inside an RTL word in order but not the word order, so each rendered line (preview, page list, breadcrumb) gets its words put in visual order while letters stay logical; RTL preview blocks right-aligned; Notion's single line breaks kept. Tested in `tests/test_bidi.py`.
- Notion calls run in background workers; UI never blocks. Tested on Linux (GNOME); no Windows. README.md "How it works" has details.
