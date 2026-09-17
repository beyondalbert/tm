from __future__ import annotations

from tm.clipboard import copy_to_clipboard


def test_empty_text_is_not_copied() -> None:
    assert copy_to_clipboard("") is False


def test_windows_path_uses_the_native_backend(monkeypatch) -> None:
    captured: list[str] = []

    class FakeSys:
        platform = "win32"

    monkeypatch.setattr("tm.clipboard.sys", FakeSys)

    def fake(text: str) -> bool:
        captured.append(text)
        return True

    monkeypatch.setattr("tm.clipboard._copy_windows", fake)
    assert copy_to_clipboard("hello") is True
    assert captured == ["hello"]


def test_linux_falls_back_to_a_clipboard_tool(monkeypatch) -> None:
    class FakeSys:
        platform = "linux"

    monkeypatch.setattr("tm.clipboard.sys", FakeSys)
    monkeypatch.setattr("tm.clipboard._pipe", lambda argv, text: argv[0] == "wl-copy")
    assert copy_to_clipboard("hello") is True


def test_returns_false_when_no_backend_exists(monkeypatch) -> None:
    class FakeSys:
        platform = "linux"

    monkeypatch.setattr("tm.clipboard.sys", FakeSys)
    monkeypatch.setattr("tm.clipboard._pipe", lambda argv, text: False)
    assert copy_to_clipboard("hello") is False
