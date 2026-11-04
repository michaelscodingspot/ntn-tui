import pytest

from notion_tui import editor


def test_edit_text_runs_editor_with_arguments_and_returns_new_text(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "sed -i s/old/new/")
    assert editor.edit_text("old body\n") == (0, "new body\n")


def test_visual_wins_over_editor_and_exit_status_is_returned(monkeypatch):
    monkeypatch.setenv("VISUAL", "false")
    monkeypatch.setenv("EDITOR", "true")
    assert editor.edit_text("text") == (1, "text")


def test_missing_editor_raises_oserror(monkeypatch):
    monkeypatch.setenv("VISUAL", "no-such-editor-xyz")
    with pytest.raises(OSError):
        editor.edit_text("text")


def test_keep_unsaved_writes_text_under_page_id(monkeypatch, tmp_path):
    monkeypatch.setattr(editor, "UNSAVED_DIR", tmp_path / "unsaved")
    path = editor.keep_unsaved("abc", "body")
    assert path == tmp_path / "unsaved" / "abc.md" and path.read_text() == "body"
