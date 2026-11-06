import asyncio
import subprocess

from textual.app import App
from textual.widgets import Checkbox

from notion_tui import narrow
from notion_tui.modals import OptionsScreen
from notion_tui.options import Options


def in_nui_window(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1")
    monkeypatch.setenv("KITTY_LISTEN_ON", "unix:/run/user/1/notion-tui.sock")


def test_available_only_in_the_nui_window(monkeypatch):
    in_nui_window(monkeypatch)
    assert narrow.available()
    monkeypatch.setenv("KITTY_LISTEN_ON", "unix:/run/user/1/other.sock")
    assert not narrow.available()
    monkeypatch.delenv("KITTY_LISTEN_ON")
    assert not narrow.available()


def test_is_on_follows_the_flag_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert not narrow.is_on()
    (tmp_path / "notion-tui").mkdir()
    (tmp_path / "notion-tui" / "narrow").touch()
    assert narrow.is_on()


def test_set_on_sends_the_wanted_state(monkeypatch):
    in_nui_window(monkeypatch)
    sent = []

    def run(command, **_):
        sent.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert narrow.set_on(True)
    assert narrow.set_on(False)
    assert [c[-1] for c in sent] == ["narrow_toggle=on", "narrow_toggle=off"]
    assert sent[0][:4] == ["kitten", "@", "--to", "unix:/run/user/1/notion-tui.sock"]


def test_set_on_reports_failure(monkeypatch):
    def run(command, **_):
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert not narrow.set_on(True)

    def missing(command, **_):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", missing)
    assert not narrow.set_on(True)


def with_options_dialog(check):
    async def run():
        app = App()
        async with app.run_test() as pilot:
            await app.push_screen(OptionsScreen(Options()))
            await pilot.pause()
            await check(app, pilot, app.screen.query_one("#narrow", Checkbox))

    asyncio.run(run())


def test_options_checkbox_changes_the_setting(monkeypatch):
    sent = []
    monkeypatch.setattr(narrow, "available", lambda: True)
    monkeypatch.setattr(narrow, "is_on", lambda: False)
    monkeypatch.setattr(narrow, "set_on", lambda on: sent.append(on) or True)

    async def check(app, pilot, box):
        assert not box.disabled and not box.value
        box.toggle()
        await pilot.pause()
        assert box.value

    with_options_dialog(check)
    assert sent == [True]


def test_options_checkbox_reverts_when_kitty_refuses(monkeypatch):
    sent = []
    monkeypatch.setattr(narrow, "available", lambda: True)
    monkeypatch.setattr(narrow, "is_on", lambda: False)
    monkeypatch.setattr(narrow, "set_on", lambda on: sent.append(on) or False)

    async def check(app, pilot, box):
        box.toggle()
        await pilot.pause()
        assert not box.value

    with_options_dialog(check)
    assert sent == [True]


def test_options_checkbox_is_disabled_outside_the_nui_window(monkeypatch):
    monkeypatch.setattr(narrow, "available", lambda: False)
    monkeypatch.setattr(narrow, "is_on", lambda: False)

    async def check(app, pilot, box):
        assert box.disabled

    with_options_dialog(check)
