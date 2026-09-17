from __future__ import annotations

from pathlib import Path

from tm.config import (
    credentials_path,
    load_credentials,
    load_settings,
    sanitize_api_key,
    save_credential,
    settings_path,
)


def test_config_dir_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path / "cfg"))
    assert settings_path() == tmp_path / "cfg" / "settings.toml"
    assert credentials_path() == tmp_path / "cfg" / "credentials.toml"


def test_load_settings_reads_toml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_text('provider = "qwen"\nmodel = "qwen-max"\n')
    settings = load_settings()
    assert settings.provider == "qwen"
    assert settings.model == "qwen-max"


def test_load_settings_tolerates_bom(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_bytes(b'\xef\xbb\xbfprovider = "qwen"\n')
    assert load_settings().provider == "qwen"


def test_save_and_load_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    assert load_credentials() == {}

    path = save_credential("deepseek", "sk-abc")
    assert path == tmp_path / "credentials.toml"
    assert load_credentials() == {"deepseek": "sk-abc"}

    save_credential("qwen", "sk-qwen")
    assert load_credentials() == {"deepseek": "sk-abc", "qwen": "sk-qwen"}


def test_load_credentials_missing_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path / "nope"))
    assert load_credentials() == {}


def test_sanitize_api_key_strips_whitespace() -> None:
    assert sanitize_api_key("  sk-abc123\n") == "sk-abc123"
    assert sanitize_api_key('"sk-abc123"') == "sk-abc123"


def test_sanitize_api_key_collapses_repetition() -> None:
    key = "sk-" + "a" * 32
    assert sanitize_api_key(key * 3) == key
    assert sanitize_api_key(key * 2) == key
    assert sanitize_api_key(key) == key


def test_load_credentials_repairs_repeated_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    key = "sk-" + "b" * 32
    (tmp_path / "credentials.toml").write_text(
        f'[providers]\ndeepseek = "{key * 3}"\n', encoding="utf-8"
    )
    assert load_credentials()["deepseek"] == key
