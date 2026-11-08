"""Start an interactive Claude Code session on a task taken from a database cell (ctrl+o in Edit mode).

The session opens in herdr (the terminal workspace manager running in kitty), in a repo the
user picks:

- Obics: a free /c/Obics/mrXXX clone, claimed from the same pool jira-board and the mail task
  use (`jira-board --claim-clone`), pulled, and opened in a new herdr space.
- any other repo: a new git worktree made by `herdr worktree create`, grouped under the space
  that has the repo's main checkout open. With no such space, one is created first.

Claude starts in the new space's pane (`herdr agent start`) and gets "task: <cell text>" as its
first prompt (`herdr agent prompt`), as edited by the user. Then herdr's kitty window is raised.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import desktop

OBICS = "Obics"


@dataclass(frozen=True)
class Repo:
    label: str
    path: str  # "" for Obics: a clone is claimed when the session starts


REPOS = [
    Repo("aut", "/c/aut"),
    Repo("notion-tui", "/c/notion-tui"),
    Repo("jira-board", "/c/jira-board"),
    Repo(OBICS, ""),
]

_SOCKET = Path(os.environ.get("HERDR_SOCKET_PATH") or Path.home() / ".config" / "herdr" / "herdr.sock")
_AGENT_START_TIMEOUT_MS = 60000
_ANSWER_TIMEOUT_MS = 300000  # for the user to answer Claude's startup question
_SLUG_WORDS = 5
_SLUG_MAX = 40


def task_prompt(text: str) -> str:
    return f"task: {text.strip()}"


def repo_path(text: str) -> str:
    """A repo typed for "other": a folder name under /c/, or a full path."""
    text = text.strip().rstrip("/")
    return str(Path(text).expanduser()) if text.startswith(("/", "~")) else f"/c/{text}"


def branch_slug(text: str, taken: set[str]) -> str:
    """A branch name from the first words of the task, with -2, -3 … when it is taken."""
    words = re.findall(r"[a-z0-9]+", text.casefold())[:_SLUG_WORDS]
    slug = "-".join(words)[:_SLUG_MAX].strip("-") or "task"
    name, n = slug, 2
    while name in taken:
        name, n = f"{slug}-{n}", n + 1
    return name


def main_space(worktree_list: dict) -> str | None:
    """The herdr space showing the repo's main checkout, from `herdr worktree list`."""
    for tree in worktree_list.get("worktrees", []):
        if not tree.get("is_linked_worktree") and tree.get("open_workspace_id"):
            return tree["open_workspace_id"]
    return None


def agent_name(slug: str) -> str:
    """Unique among live agents and within herdr's [a-z][a-z0-9_-]{0,31}."""
    base = re.sub(r"[^a-z0-9-]", "", slug)[:24].strip("-") or "task"
    if not base[0].isalpha():
        base = "t" + base[:23]
    return f"{base}-{secrets.token_hex(2)}"


async def _run(*args: str, cwd: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip(), err.decode(errors="replace").strip()


class HerdrError(RuntimeError):
    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code  # herdr's error code, e.g. agent_not_ready


async def _herdr(*args: str) -> dict:
    """One herdr CLI call; its JSON result. Raises HerdrError with herdr's message on failure."""
    code, out, err = await _run("herdr", *args)
    if code:
        try:
            error = json.loads(err)["error"]
            message, kind = error["message"], error.get("code", "")
        except (ValueError, KeyError, TypeError):
            message, kind = err or out or f"exit status {code}", ""
        raise HerdrError(f"herdr {args[0]} {args[1]}: {message}", kind)
    try:
        return json.loads(out)["result"]
    except (ValueError, KeyError) as error:
        raise HerdrError(f"herdr {args[0]} {args[1]}: unexpected output") from error


async def _taken_branches(repo: str) -> set[str]:
    code, out, _ = await _run("git", "-C", repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    taken = set(out.splitlines()) if code == 0 else set()
    worktrees = Path.home() / ".herdr" / "worktrees" / Path(repo).name
    if worktrees.is_dir():
        taken |= {p.name for p in worktrees.iterdir()}
    return taken


async def _open_obics() -> tuple[str, str]:
    """Claim a free mr clone, pull it, open it in a new space. Returns (where, pane ID)."""
    code, out, err = await _run("jira-board", "--claim-clone")
    if code or not out:
        raise RuntimeError(err or "no free Obics clone")
    clone = out.splitlines()[-1]
    try:
        await _run("gp", cwd=clone)  # same catch-up pull cdmrr does; a failure is not fatal
    except OSError:
        pass
    result = await _herdr("workspace", "create", "--cwd", clone, "--label", Path(clone).name, "--focus")
    return clone, result["root_pane"]["pane_id"]


async def _open_worktree(repo: str, text: str) -> tuple[str, str]:
    """A new worktree of the repo, in a space grouped under its main checkout. Returns (where, pane ID)."""
    code, top, _ = await _run("git", "-C", repo, "rev-parse", "--show-toplevel")
    if code:
        raise RuntimeError(f"{repo} isn't a git repo")
    space = main_space(await _herdr("worktree", "list", "--cwd", top))
    if space is None:
        created = await _herdr("workspace", "create", "--cwd", top, "--label", Path(top).name, "--no-focus")
        space = created["workspace"]["workspace_id"]
    branch = branch_slug(text, await _taken_branches(top))
    result = await _herdr("worktree", "create", "--workspace", space, "--branch", branch, "--focus")
    return f"{Path(top).name} worktree {branch}", result["root_pane"]["pane_id"]


async def start_session(repo: Repo, text: str, prompt: str, say: Callable[[str], None] = lambda _: None) -> str:
    """Open the session on `prompt`; the worktree branch is named from the cell `text`. Returns
    what happened. Raises RuntimeError when it could not start.

    Claude asks whether to trust a folder it hasn't seen, which every new worktree is. That
    question is left to the user: herdr's window is raised, `say` tells them, and the prompt
    is sent once Claude is ready."""
    if not _SOCKET.exists():
        raise RuntimeError("herdr isn't running")
    if repo.label == OBICS:
        where, pane = await _open_obics()
    else:
        where, pane = await _open_worktree(repo.path, text)
    name = agent_name(branch_slug(text, set()))
    try:
        await _herdr("agent", "start", name, "--kind", "claude", "--pane", pane,
                     "--timeout", str(_AGENT_START_TIMEOUT_MS), "--", "--dangerously-skip-permissions")
    except HerdrError as error:
        if error.code != "agent_not_ready":
            raise
        await desktop.raise_herdr_window()
        say(f"Answer Claude's question in herdr ({where}); the task follows.")
        await _herdr("agent", "wait", name, "--until", "idle", "--timeout", str(_ANSWER_TIMEOUT_MS))
    await _herdr("agent", "prompt", name, prompt)
    await desktop.raise_herdr_window()
    return f"Claude started in herdr: {where}"
