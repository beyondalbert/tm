from __future__ import annotations

import os
from pathlib import Path

from tm.im import daemon
from tm.im.cli import _forward_start_args


def test_pid_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    assert daemon.read_pid() is None
    daemon.write_pid(4321)
    assert daemon.read_pid() == 4321
    daemon.clear_pid()
    assert daemon.read_pid() is None


def test_is_alive_self() -> None:
    assert daemon.is_alive(os.getpid()) is True
    assert daemon.is_alive(0) is False


def test_read_tail(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    daemon.log_path().parent.mkdir(parents=True, exist_ok=True)
    daemon.log_path().write_text("a\nb\nc\nd\n", encoding="utf-8")
    assert daemon.read_tail(2) == ["c", "d"]
    assert daemon.read_tail(10) == ["a", "b", "c", "d"]


def test_forward_start_args_only_includes_provided() -> None:
    args = _forward_start_args(
        {"base_url": "http://h/v1", "model": "m", "config": None}, workers=2
    )
    assert args == ["--base-url", "http://h/v1", "--model", "m"]


def test_forward_start_args_includes_workers_when_non_default() -> None:
    args = _forward_start_args({"base_url": None}, workers=4)
    assert args == ["--workers", "4"]
