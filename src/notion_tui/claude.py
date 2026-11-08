"""Start an interactive Claude Code session on a task taken from a database cell (ctrl+o in Edit mode).

Same shape as jira-board's ctrl-o and aerc's "task from email": the prompt goes to a temp
file and a new Ptyxis tab runs my-automations' mail-claude-session.sh on that file. That
script asks in the tab which folder to run in (/c/Obics/li, /c/aut, /c/Obics/int, a free mr
clone, or the current folder), builds a tmux session running Claude with the prompt as its
first message, and deletes the file. The prompt can't go on the command line because Ptyxis
word-splits its -x argument.

With no Ptyxis or no display the session is built detached on the tmux server, in the
current folder, and its name is reported so it can be attached to.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

SESSION_SCRIPT = Path("/c/aut/Configurations/mail/bin/mail-claude-session.sh")


def task_prompt(text: str) -> str:
    return f"task: {text.strip()}\n"


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def start_session(text: str) -> str:
    """Open the session; returns what happened. Raises RuntimeError when it could not start."""
    if not SESSION_SCRIPT.exists():
        raise RuntimeError(f"{SESSION_SCRIPT} not found")
    fd, path = tempfile.mkstemp(prefix="notion-tui-claude-", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(task_prompt(text))
    try:
        if shutil.which("ptyxis") and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            code, out = await _run("ptyxis", "--tab", "-d", os.getcwd(), "-x", f"{SESSION_SCRIPT} {path}")
            if code:
                raise RuntimeError(out or f"ptyxis exit status {code}")
            return "Claude session opened in a new Ptyxis tab"
        code, out = await _run(str(SESSION_SCRIPT), path, "--no-attach")
        if code:
            raise RuntimeError(out or f"exit status {code}")
        return f"Claude session started detached: tmux attach -t {out.splitlines()[-1]}"
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
