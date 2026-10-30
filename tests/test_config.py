from __future__ import annotations

from pathlib import Path

import pytest

from tm.config import (
    Settings,
    compaction_for,
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
    (tmp_path / "settings.toml").write_text(
        'provider = "qwen"\nmodel = "qwen-max"\n'
        "compact_max_tokens = 150000\n"
        "prune_tool_output = false\n"
        "prune_tool_output_keep_turns = 4\n"
        "prune_tool_output_min_chars = 2000\n"
        "prune_tool_output_keep_chars = 100\n"
        'spill_dir = "D:/tmp/spill"\n'
        "read_dedupe = false\n"
    )
    settings = load_settings()
    assert settings.provider == "qwen"
    assert settings.model == "qwen-max"
    assert settings.compact_max_tokens == 150000
    assert settings.prune_tool_output is False
    assert settings.prune_tool_output_keep_turns == 4
    assert settings.prune_tool_output_min_chars == 2000
    assert settings.prune_tool_output_keep_chars == 100
    assert settings.spill_dir == "D:/tmp/spill"
    assert settings.read_dedupe is False


def test_load_settings_reads_model_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_text(
        "compact_max_tokens = 150000\n"
        "[model_overrides.\"local/big\"]\n"
        "compact_max_tokens = 100000\n"
        "compact_keep_tokens = 12000\n"
    )
    settings = load_settings()
    assert settings.model_overrides == {
        "local/big": {"compact_max_tokens": 100000, "compact_keep_tokens": 12000}
    }


def test_compaction_for_merges_model_overrides() -> None:
    settings = Settings(
        compact_threshold=0.8,
        compact_keep_recent=6,
        compact_keep_tokens=20000,
        compact_max_tokens=150000,
        model_overrides={
            "local/big": {
                "compact_threshold": 0.5,
                "compact_keep_recent": 3,
                "compact_keep_tokens": 12000,
                "compact_max_tokens": 100000,
            }
        },
    )
    limits = compaction_for(settings, "local", "big")
    assert limits.threshold == 0.5
    assert limits.keep_recent == 3
    assert limits.keep_tokens == 12000
    assert limits.max_tokens == 100000

    other = compaction_for(settings, "local", "other")
    assert other.threshold == 0.8
    assert other.keep_tokens == 20000
    assert other.max_tokens == 150000


def test_compaction_for_rejects_invalid_overrides() -> None:
    settings = Settings(model_overrides={"local/big": {"compact_max_tokens": -1}})
    with pytest.raises(ValueError, match="compact_max_tokens"):
        compaction_for(settings, "local", "big")



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
