# notion-tui

Terminal UI for Notion, built on the [`ntn`](https://developers.notion.com) CLI.
Layout: page list on the left, Markdown preview on the right. The preview's top line
shows the page's path, e.g. `Notes / Quick Notes`.
Databases are listed too (marked `▦`); their preview is a table of the first 100 rows.
Edit and trash work on pages only.

![Page list with Markdown preview](docs/screenshots/main.png)

| Database preview | Search |
|---|---|
| ![Database shown as a table](docs/screenshots/database.png) | ![Search results](docs/screenshots/search.png) |

## Run

```
uv run notion-tui
```

Needs `ntn` on `PATH` and logged in (or `NOTION_API_TOKEN` set for `ntn pages`).

## Shortcut Keys

| Key            | Action                                   |
|----------------|------------------------------------------|
| `j` / `k`      | move in list (scroll in preview)         |
| `g` / `G`      | top / bottom (bottom loads more)         |
| `enter`        | open page full screen                    |
| `esc`          | back to list / clear search              |
| `tab`          | cycle search → list → preview            |
| `/`            | search (instant from cache, then Notion) |
| `e`            | edit page in `$EDITOR` (`ntn pages edit`)|
| `n`            | new page in `$EDITOR` (`ntn pages create`)|
| `d`            | trash page (asks first)                  |
| `o`            | open page in Notion web app, focus tab   |
| `r`            | refresh                                  |
| `,`            | options                                  |
| `?`            | help panel                               |
| `q`            | quit                                     |

## Options

Saved in `~/.config/notion-tui/options.json` (changed with `,`).

| Option                   | Values                                   |
|--------------------------|------------------------------------------|
| Show database entries    | `yes`, `never`, `show-non-empty` (default)|

Database entries are pages that are rows of a database. `show-non-empty` lists only
entries whose page has content (a block that isn't an empty line); the status line counts
the hidden ones.

## How it works

- Every Notion call goes through `ntn`: `ntn api v1/search` for lists and search (pages and
  databases), `ntn api v1/data_sources/<id>/query` for database rows,
  `ntn pages get/edit/create/trash` for page actions.
- Calls run in the background; the screen never waits on Notion.
- SQLite cache at `~/.cache/notion-tui/cache.db` (`pages`, `page_content`) makes startup
  instant and previews appear before Notion answers. A cached preview is reused while the
  page's `last_edited` is unchanged. The row below the cursor is prefetched.
- Whether an entry has content is checked once per edit with
  `ntn api v1/blocks/<id>/children` (2 at a time, to stay under Notion's rate limit) and kept
  in `pages.has_body`. A new `last_edited` clears it, so the next list load checks again.
- The path comes from each page's parent. Parents are cached in the `nodes` table; missing ones
  are fetched once with `ntn api v1/pages|databases|data_sources|blocks/<id>`.
- `o` raises the browser window through the GNOME Shell "Window Calls" extension
  (D-Bus `org.gnome.Shell.Extensions.Windows`), since GNOME on Wayland blocks focus
  requests from background apps. Without the extension the page opens unfocused.
