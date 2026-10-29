from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from tm.im import daemon
from tm.im.cli import _format_setting_value, _mask_secret, _settings_lines, app
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


def test_mask_secret_hides_the_key() -> None:
    assert _mask_secret("") == "(not set)"
    assert _mask_secret("short") == "*****"
    masked = _mask_secret("sk_live_" + "a" * 40)
    assert masked == "sk_liv...aaaa (48 chars)"


def test_format_setting_value_types() -> None:
    assert _format_setting_value("api_key", "sk_live_abcdef") == "sk_liv...cdef (14 chars)"
    assert _format_setting_value("ws_url", None) == "(not set)"
    assert _format_setting_value("allow_tools", "full") == '"full"'
    assert _format_setting_value("reply_max_chars", 4000) == "4000"
    assert _format_setting_value("owner_handles", ["@a", "@b"]) == '["@a", "@b"]'


def test_settings_lines_include_every_field_and_mask_secrets() -> None:
    settings = ImSettings(
        api_key="sk_live_" + "a" * 40,
        owner_handles=["@alice"],
        ws_url=None,
        system_prompt="line one\nline two",
    )

    lines = _settings_lines(settings)
    text = "\n".join(lines)

    for name in ImSettings.__dataclass_fields__:
        assert f"  {name} = " in text, name
    assert "sk_live_" + "a" * 40 not in text
    assert "sk_liv...aaaa (48 chars)" in text
    assert 'owner_handles = ["@alice"]' in text
    assert "ws_url = (not set)" in text
    assert 'system_prompt = "line one\\nline two"' in text


def test_config_list_shows_all_fields_and_masks_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    _create(tmp_path)

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    for name in ImSettings.__dataclass_fields__:
        assert f"{name} =" in result.output, name
    assert "sk_live_test" not in result.output
    assert "sk_liv...test (12 chars)" in result.output
