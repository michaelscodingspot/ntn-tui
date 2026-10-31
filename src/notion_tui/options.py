"""User options, kept in $XDG_CONFIG_HOME/notion-tui/options.json (default ~/.config/notion-tui/)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path

# Which pages that are entries (rows) of a database appear in the list.
DATABASE_ENTRIES = ("yes", "never", "show-non-empty")


def default_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "notion-tui" / "options.json"


@dataclass
class Options:
    database_entries: str = "show-non-empty"

    path: Path | None = None  # where to save; not itself an option

    @classmethod
    def load(cls, path: Path | None = None) -> Options:
        path = path or default_path()
        options = cls(path=path)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return options
        if data.get("database_entries") in DATABASE_ENTRIES:
            options.database_entries = data["database_entries"]
        return options

    def save(self) -> None:
        path = self.path or default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "path"}
        path.write_text(json.dumps(data, indent=2) + "\n")

