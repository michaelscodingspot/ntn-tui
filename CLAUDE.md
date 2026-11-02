# notion-tui

Terminal UI for Notion (Python 3.13, Textual, run with `uv run notion-tui`): page list left, Markdown preview right, edit in `$EDITOR`.

- `src/notion_tui/app.py` — Textual app: list, preview, search, confirm/options modals, key bindings.
- `src/notion_tui/ntn.py` — async wrapper over the `ntn` CLI; the only path to Notion (search, data-source query, pages get/edit/create/trash).
- `src/notion_tui/cache.py` — SQLite cache `~/.cache/notion-tui/cache.db` (`pages`, `page_content`, `nodes`) for instant startup and previews.
- `src/notion_tui/options.py` — user options in `~/.config/notion-tui/options.json`.
- `src/notion_tui/desktop.py` — opens a page in the browser, raises the window via GNOME "Window Calls" D-Bus extension.
- `src/notion_tui/bidi.py` — right-to-left text (Hebrew, Arabic): reorders each rendered line into visual order, since terminals draw cells left to right; RTL preview paragraphs are right-aligned.
- Notion calls run in background workers; UI never blocks. Tested on Linux (GNOME); no Windows. README.md "How it works" has details.
