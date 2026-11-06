"""Narrow width: keep the app in the middle 70% of a screen-wide window.

The `nui` launcher opens the app in its own kitty window, and kitty does the work there:
my-automations' narrow-window-watcher.py sets the margins, keeps them right as the window
resizes and saves the setting. It also owns Ctrl+Alt+W, so this module only reads the saved
setting and asks that watcher to change it. Any other terminal has no margins to set.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_TIMEOUT = 2.0  # seconds; kitty answers at once when the window is alive


def flag_path() -> Path:
    """The file the watcher keeps while the setting is on, named after the window class."""
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "notion-tui" / "narrow"


def _socket() -> str:
    return f"unix:{os.environ.get('XDG_RUNTIME_DIR', '')}/notion-tui.sock"


def available() -> bool:
    """Whether the app runs in the window `nui` opened, the only one with the watcher."""
    return os.environ.get("KITTY_LISTEN_ON") == _socket()


def is_on() -> bool:
    return flag_path().exists()


def set_on(on: bool) -> bool:
    """Ask the watcher to turn the setting on or off. False if kitty could not be reached."""
    value = "on" if on else "off"
    command = ["kitten", "@", "--to", _socket(), "set-user-vars", f"narrow_toggle={value}"]
    try:
        return subprocess.run(command, capture_output=True, timeout=_TIMEOUT, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
