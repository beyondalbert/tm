from __future__ import annotations

from pathlib import Path

import tm.update as update


def test_parse_version() -> None:
    assert update.parse_version("0.2.1") == (0, 2, 1)
    assert update.parse_version("1.10.0") == (1, 10, 0)
    assert update.parse_version("0.3.0rc1") == (0, 3, 0)


def test_is_newer() -> None:
    assert update.is_newer("0.2.1", "0.2.0") is True
    assert update.is_newer("0.2.0", "0.2.0") is False
    assert update.is_newer("1.0.0", "0.99.99") is True


def test_latest_version_parses_pypi(monkeypatch) -> None:
    monkeypatch.setattr(
        update, "_fetch", lambda url, timeout: b'{"info": {"version": "9.9.9"}}'
    )
    assert update.latest_version() == "9.9.9"


def test_latest_version_prefers_the_release_list(monkeypatch) -> None:
    payload = b'{"info": {"version": "0.2.0"}, "releases": {"0.2.0": [], "0.2.1": []}}'
    monkeypatch.setattr(update, "_fetch", lambda url, timeout: payload)
    assert update.latest_version() == "0.2.1"


def test_latest_version_none_when_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(update, "_fetch", lambda url, timeout: None)
    assert update.latest_version() is None


def test_upgrade_argv_prefers_uv_tool(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "uv-receipt.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        update.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "uv" else None
    )
    assert update.upgrade_argv(tmp_path) == ["/usr/bin/uv", "tool", "upgrade", "the-machine"]


def test_upgrade_argv_uses_pip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    monkeypatch.setattr(update, "_has_pip", lambda: True)
    argv = update.upgrade_argv(tmp_path)
    assert argv is not None
    assert argv[1:] == ["-m", "pip", "install", "--upgrade", "the-machine"]


def test_run_update_up_to_date(monkeypatch) -> None:
    monkeypatch.setattr(update, "_current_version", lambda: "0.2.1")
    monkeypatch.setattr(update, "latest_version", lambda: "0.2.1")
    messages: list[str] = []
    assert update.run_update(messages.append) == 0
    assert "up to date" in messages[-1]


def test_run_update_runs_the_upgrade(monkeypatch) -> None:
    monkeypatch.setattr(update, "_current_version", lambda: "0.2.0")
    monkeypatch.setattr(update, "latest_version", lambda: "0.2.1")
    monkeypatch.setattr(update, "upgrade_argv", lambda prefix=None: ["pip", "upgrade"])
    recorded: dict[str, list[str]] = {}

    def fake_call(argv: list[str]) -> int:
        recorded["argv"] = argv
        return 0

    monkeypatch.setattr(update.subprocess, "call", fake_call)
    messages: list[str] = []
    assert update.run_update(messages.append, background=False) == 0
    assert recorded["argv"] == ["pip", "upgrade"]


def test_run_update_background(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update, "_current_version", lambda: "0.2.0")
    monkeypatch.setattr(update, "latest_version", lambda: "0.2.1")
    monkeypatch.setattr(update, "upgrade_argv", lambda prefix=None: ["pip", "upgrade"])
    log = tmp_path / "update.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setattr(update, "_start_background_upgrade", lambda argv: log)
    messages: list[str] = []
    assert update.run_update(messages.append, background=True) == 0
    assert "background" in messages[-1]
