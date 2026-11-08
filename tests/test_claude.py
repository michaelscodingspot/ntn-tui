from notion_tui.claude import agent_name, branch_slug, main_space, repo_path, task_prompt


def test_task_prompt():
    assert task_prompt("  fix the login page ") == "task: fix the login page"


def test_branch_slug_takes_the_first_words():
    assert branch_slug("Fix the login page, it's broken on Safari!", set()) == "fix-the-login-page-it"


def test_branch_slug_counts_up_when_taken():
    assert branch_slug("fix login", {"fix-login", "fix-login-2"}) == "fix-login-3"


def test_branch_slug_of_no_letters():
    assert branch_slug("שלום ?!", set()) == "task"


def test_repo_path():
    assert repo_path("blog/") == "/c/blog"
    assert repo_path("/home/x/repo") == "/home/x/repo"


def test_main_space_is_the_open_main_checkout():
    listing = {"worktrees": [
        {"path": "/c/r/wt", "is_linked_worktree": True, "open_workspace_id": "w3"},
        {"path": "/c/r", "is_linked_worktree": False, "open_workspace_id": "w1"},
    ]}
    assert main_space(listing) == "w1"


def test_main_space_none_when_main_not_open():
    listing = {"worktrees": [{"path": "/c/r", "is_linked_worktree": False}]}
    assert main_space(listing) is None


def test_agent_name_fits_herdr_rules():
    import re

    for slug in ("fix-login", "2026-plan", "", "a" * 60):
        assert re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", agent_name(slug))
