from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from tm.im import daemon
from tm.im.cli import _format_setting_value, _mask_secret, _settings_lines, app
from tm.im.config import (
    ImSettings,
    instance_dir,
    load_im_settings,
    named_config_path,
    save_im_settings,
)

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


def test_config_setup_sets_provider_model_and_stores_key(
    tmp_path: Path, monkeypatch
) -> None:
    from unittest.mock import patch

    from tm.config import load_credentials

    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = _create(tmp_path)

    with patch("tm.im.cli.typer.prompt", return_value="sk-test-key"):
        result = runner.invoke(
            app,
            [
                "config",
                "model",
                "setup",
                "--config",
                "alice",
                "-p",
                "deepseek",
                "-m",
                "deepseek-chat",
            ],
        )

    assert result.exit_code == 0, result.output
    settings = load_im_settings(path, apply_env=False)
    assert settings.provider == "deepseek"
    assert settings.model == "deepseek-chat"
    assert load_credentials()["deepseek"] == "sk-test-key"
    assert "sk-test-key" not in result.output


def test_config_setup_no_login_skips_key_prompts(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import patch

    from tm.config import load_credentials

    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = _create(tmp_path)

    with patch("tm.im.cli.typer.prompt") as prompt:
        result = runner.invoke(
            app,
            [
                "config",
                "model",
                "setup",
                "--config",
                "alice",
                "-p",
                "deepseek",
                "-m",
                "deepseek-chat",
                "--no-login",
            ],
        )

    assert result.exit_code == 0, result.output
    prompt.assert_not_called()
    assert load_im_settings(path, apply_env=False).model == "deepseek-chat"
    assert "deepseek" not in load_credentials()


def test_config_setup_prompts_for_missing_tm_im_key(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import patch

    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = named_config_path("bob")
    save_im_settings(ImSettings(), path)

    with patch(
        "tm.im.cli.typer.prompt", side_effect=["sk-model-key", "sk_live_agent"]
    ):
        result = runner.invoke(
            app,
            [
                "config",
                "model",
                "setup",
                "--config",
                "bob",
                "-p",
                "deepseek",
                "-m",
                "deepseek-chat",
            ],
        )

    assert result.exit_code == 0, result.output
    settings = load_im_settings(path, apply_env=False)
    assert settings.api_key == "sk_live_agent"
    assert settings.provider == "deepseek"


def _write_custom_provider(tmp_path: Path, *, base_url: str) -> None:
    (tmp_path / "models.toml").write_text(
        f'''[providers.custom]
name = "Custom"
api = "openai-completions"
base_url = "{base_url}"
default_model = "custom-model"

[[providers.custom.models]]
id = "custom-model"
''',
        encoding="utf-8",
    )


def test_model_setup_prompts_for_custom_hosted_provider_key(
    tmp_path: Path, monkeypatch
) -> None:
    from unittest.mock import patch

    from tm.config import load_credentials

    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = _create(tmp_path)
    _write_custom_provider(tmp_path, base_url="https://api.example.com/v1")

    with patch("tm.im.cli.typer.prompt", return_value="sk-custom-key"):
        result = runner.invoke(
            app,
            [
                "config",
                "model",
                "setup",
                "--config",
                "alice",
                "-p",
                "custom",
                "-m",
                "custom-model",
            ],
        )

    assert result.exit_code == 0, result.output
    assert load_im_settings(path, apply_env=False).provider == "custom"
    assert load_credentials()["custom"] == "sk-custom-key"
    assert "stored model key for custom" in result.output
    assert "model key = configured" in result.output


def test_model_setup_does_not_prompt_for_local_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    from unittest.mock import patch

    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = _create(tmp_path)
    _write_custom_provider(tmp_path, base_url="http://127.0.0.1:11434/v1")

    with patch("tm.im.cli.typer.prompt") as prompt:
        result = runner.invoke(
            app,
            [
                "config",
                "model",
                "setup",
                "--config",
                "alice",
                "-p",
                "custom",
                "-m",
                "custom-model",
            ],
        )

    assert result.exit_code == 0, result.output
    prompt.assert_not_called()
    assert load_im_settings(path, apply_env=False).model == "custom-model"
    assert "not required (local endpoint)" in result.output


def test_config_setup_alias_still_works(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import patch

    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = _create(tmp_path)

    with patch("tm.im.cli.typer.prompt", return_value="sk-test-key"):
        result = runner.invoke(
            app,
            [
                "config",
                "setup",
                "--config",
                "alice",
                "-p",
                "deepseek",
                "-m",
                "deepseek-chat",
            ],
        )

    assert result.exit_code == 0, result.output
    assert load_im_settings(path, apply_env=False).model == "deepseek-chat"


def test_model_setup_rekey_replaces_existing_model_key(
    tmp_path: Path, monkeypatch
) -> None:
    from unittest.mock import patch

    from tm.config import load_credentials, save_credential

    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    save_credential("deepseek", "sk-old-key")
    _create(tmp_path)

    with patch("tm.im.cli.typer.prompt", return_value="sk-new-key"):
        result = runner.invoke(
            app,
            [
                "config",
                "model",
                "setup",
                "--config",
                "alice",
                "-p",
                "deepseek",
                "-m",
                "deepseek-chat",
                "--rekey",
            ],
        )

    assert result.exit_code == 0, result.output
    assert load_credentials()["deepseek"] == "sk-new-key"
    assert "replacing the stored model key" in result.output
    assert "model key = configured" in result.output
