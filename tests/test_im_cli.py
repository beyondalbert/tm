from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from tm.im import daemon
from tm.im.cli import app
from tm.im.config import ImSettings, instance_dir, named_config_path, save_im_settings

runner = CliRunner()


def _create(tmp_path: Path, name: str = "alice") -> Path:
    path = named_config_path(name)
    save_im_settings(ImSettings(api_key="sk_live_test"), path)
    return path


def _instance_with_state(path: Path) -> Path:
    base = instance_dir(str(path))
    base.mkdir(parents=True, exist_ok=True)
    (base / "im.log").write_text("log\n", encoding="utf-8")
    return base


def test_config_delete_removes_file_and_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    path = _create(tmp_path)
    base = _instance_with_state(path)

    result = runner.invoke(app, ["config", "delete", "alice", "--yes"])

    assert result.exit_code == 0, result.output
    assert not path.exists()
    assert not base.exists()


def test_config_delete_keep_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    path = _create(tmp_path)
    base = _instance_with_state(path)

    result = runner.invoke(app, ["config", "delete", "alice", "--yes", "--keep-state"])

    assert result.exit_code == 0, result.output
    assert not path.exists()
    assert (base / "im.log").exists()


def test_config_delete_refuses_running_instance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    path = _create(tmp_path)
    base = _instance_with_state(path)
    daemon.write_pid(base, os.getpid())

    result = runner.invoke(app, ["config", "delete", "alice", "--yes"])

    assert result.exit_code == 1
    assert path.exists()
    assert "stop it first" in result.output


def test_config_delete_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))

    result = runner.invoke(app, ["config", "delete", "missing", "--yes"])

    assert result.exit_code == 1
    assert "not found" in result.output


def test_config_delete_rejects_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))

    result = runner.invoke(app, ["config", "delete", "nested/alice", "--yes"])

    assert result.exit_code == 1
    assert "bare name" in result.output
