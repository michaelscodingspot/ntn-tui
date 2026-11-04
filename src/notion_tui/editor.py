"""Edit text in the user's $VISUAL / $EDITOR, the way `ntn pages edit` does, but without Notion calls.

The app fetches the page before and saves it after, while the TUI stays on screen,
so the TUI is hidden only while the editor itself runs.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

UNSAVED_DIR = Path.home() / ".cache" / "notion-tui" / "unsaved"


def editor_command() -> list[str]:
    """$VISUAL, else $EDITOR, else vi — split like a shell would ("code --wait" works)."""
    return shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")


def edit_text(text: str) -> tuple[int, str]:
    """Open `text` in the editor attached to the real terminal; returns (exit status, edited text).
    Call while the TUI is suspended."""
    fd, path = tempfile.mkstemp(prefix="notion-tui-", suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        code = subprocess.call([*editor_command(), path])
        with open(path) as f:
            return code, f.read()
    finally:
        os.unlink(path)


def keep_unsaved(page_id: str, text: str) -> Path:
    """Keep edited text that Notion refused, so the work isn't lost; returns where it is."""
    UNSAVED_DIR.mkdir(parents=True, exist_ok=True)
    path = UNSAVED_DIR / f"{page_id}.md"
    path.write_text(text)
    return path
