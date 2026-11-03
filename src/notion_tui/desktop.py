"""Open a Notion page or web link in the default browser and bring that browser window to the front.

GNOME on Wayland refuses focus requests from apps that aren't focused already, so a tab
opened from the terminal lands in a background window. The "Window Calls" GNOME Shell
extension (window-calls@domandoman.xyz) exposes window listing and activation over D-Bus;
when it is installed we use it to raise the window showing the page. Without it, the page
still opens, just without focus.

A Notion page is found by its title. A web link's title isn't known in advance, so the
browser window whose title changes (the new tab) or that appears after opening is raised.
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


def _browser_class() -> str:
    """The default browser's window class, guessed from its desktop file: google-chrome.desktop → google-chrome."""
    try:
        out = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, timeout=2
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return out.strip().removesuffix(".desktop").casefold()


def _is_browser(window: dict, browser: str) -> bool:
    if not browser:
        return True  # unknown browser: any window may be it
    names = {(window.get("wm_class") or "").casefold(), (window.get("wm_class_instance") or "").casefold()}
    return any(name and (name == browser or browser.endswith(name) or name.endswith(browser)) for name in names)


async def open_and_raise(url: str) -> bool:
    """Open a link in the browser and activate the browser window that shows it."""
    browser = await asyncio.to_thread(_browser_class)
    before = await _windows()
    open_url(url)
    if before is None:
        return False  # extension not installed: the link opens without focus
    titles = {w["id"]: w.get("title") for w in before}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RAISE_TIMEOUT
    while loop.time() < deadline:
        await asyncio.sleep(_POLL)
        windows = await _windows() or []
        changed = [
            w for w in windows
            if not w.get("focus") and _is_browser(w, browser) and titles.get(w["id"], object()) != w.get("title")
        ]
        if changed:
            return await _gdbus("Activate", str(changed[0]["id"])) is not None
    return False
