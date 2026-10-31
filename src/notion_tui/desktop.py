"""Open a Notion page in the default browser and bring that browser window to the front.

GNOME on Wayland refuses focus requests from apps that aren't focused already, so a tab
opened from the terminal lands in a background window. The "Window Calls" GNOME Shell
extension (window-calls@domandoman.xyz) exposes window listing and activation over D-Bus;
when it is installed we use it to raise the window showing the page. Without it, the page
still opens, just without focus.
"""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import webbrowser

_WINDOWS = [
    "gdbus", "call", "--session",
    "--dest", "org.gnome.Shell",
    "--object-path", "/org/gnome/Shell/Extensions/Windows",
]
_RAISE_TIMEOUT = 5.0  # seconds to wait for the browser to show the page
_POLL = 0.25


def open_url(url: str) -> None:
    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        webbrowser.open(url)


async def _gdbus(method: str, *args: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            *_WINDOWS, "--method", f"org.gnome.Shell.Extensions.Windows.{method}", *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    out, _ = await proc.communicate()
    return out.decode(errors="replace") if proc.returncode == 0 else None


async def _windows() -> list[dict] | None:
    """All windows as reported by Window Calls, or None when the extension isn't available."""
    out = await _gdbus("List")
    if out is None:
        return None
    try:
        return json.loads(ast.literal_eval(out.strip())[0])
    except (ValueError, SyntaxError, IndexError):
        return None


def _shows_page(window: dict, title: str) -> bool:
    name = window.get("title") or ""
    return not window.get("focus") and "Notion" in name and title.casefold() in name.casefold()


async def raise_page_window(title: str) -> bool:
    """Wait for a window showing the page (browser titles are "<page> | Notion - <browser>") and activate it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RAISE_TIMEOUT
    while loop.time() < deadline:
        windows = await _windows()
        if windows is None:
            return False  # extension not installed: nothing we can do
        match = next((w for w in windows if _shows_page(w, title)), None)
        if match:
            return await _gdbus("Activate", str(match["id"])) is not None
        await asyncio.sleep(_POLL)
    return False
