from __future__ import annotations

import os
from pathlib import Path

from tm.im import daemon
from tm.im.cli import _forward_start_args
from tm.im.config import instance_dir


def test_pid_roundtrip(tmp_path: Path) -> None:
    base = tmp_path / "inst"
    assert daemon.read_pid(base) is None
    daemon.write_pid(base, 4321)
    assert daemon.read_pid(base) == 4321
    daemon.clear_pid(base)
    assert daemon.read_pid(base) is None


def test_is_alive_self() -> None:
    assert daemon.is_alive(os.getpid()) is True
    assert daemon.is_alive(0) is False


def test_read_tail(tmp_path: Path) -> None:
    base = tmp_path / "inst"
    daemon.log_path(base).parent.mkdir(parents=True, exist_ok=True)
    daemon.log_path(base).write_text("a\nb\nc\nd\n", encoding="utf-8")
    assert daemon.read_tail(base, 2) == ["c", "d"]
    assert daemon.read_tail(base, 10) == ["a", "b", "c", "d"]


def test_instances_are_isolated_by_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    a = instance_dir(str(tmp_path / "a.toml"))
    b = instance_dir(str(tmp_path / "b.toml"))
    assert a != b
    assert instance_dir(str(tmp_path / "a.toml")) == a
    assert instance_dir(None) == tmp_path / "im"


def test_forward_start_args_only_includes_provided() -> None:
    args = _forward_start_args(
        {"base_url": "http://h/v1", "model": "m", "config": None}, workers=2
    )
    assert args == ["--base-url", "http://h/v1", "--model", "m"]


def test_forward_start_args_includes_workers_when_non_default() -> None:
    args = _forward_start_args({"base_url": None}, workers=4)
    assert args == ["--workers", "4"]
