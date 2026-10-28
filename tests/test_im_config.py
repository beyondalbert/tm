from __future__ import annotations

from pathlib import Path

import pytest

from tm.im.config import (
    ImSettings,
    apply_setting,
    load_im_settings,
    named_config_path,
    resolve_config,
    save_im_settings,
)


def test_load_from_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TM_IM_API_KEY", raising=False)
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "im.toml").write_text(
        'api_key = "sk_live_file"\nallow_tools = "read_only"\n', encoding="utf-8"
    )
    settings = load_im_settings()
    assert settings.api_key == "sk_live_file"
    assert settings.allow_tools == "read_only"


def test_env_overrides_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "im.toml").write_text('api_key = "file"\n', encoding="utf-8")
    monkeypatch.setenv("TM_IM_API_KEY", "env")
    monkeypatch.setenv("TM_IM_ACK_REPLY", "收到")
    monkeypatch.setenv("TM_IM_BUSY_REPLY", "处理中")
    settings = load_im_settings()
    assert settings.api_key == "env"
    assert settings.ack_reply == "收到"
    assert settings.busy_reply == "处理中"


def test_unknown_choice_falls_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TM_IM_ALLOW_TOOLS", raising=False)
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "im.toml").write_text('allow_tools = "everything"\n', encoding="utf-8")
    assert load_im_settings().allow_tools == "full"


def test_default_base_url() -> None:
    assert ImSettings().base_url == "http://tmim.stkg.ltd/v1"


def test_realtime_address_defaults() -> None:
    settings = ImSettings(base_url="https://host:8080/v1")
    assert settings.host == "host"
    assert settings.realtime_address() == ("host", 8090)


def test_realtime_address_override() -> None:
    settings = ImSettings(tcp_addr="example.com:9000")
    assert settings.realtime_address() == ("example.com", 9000)


def test_save_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "im.toml"
    settings = ImSettings(
        api_key="sk_live_k",
        allow_tools="none",
        owner_handles=["@alice"],
        tcp_addr="tcp.example:8090",
    )
    save_im_settings(settings, path)
    loaded = load_im_settings(path)
    assert loaded.api_key == "sk_live_k"
    assert loaded.allow_tools == "none"
    assert loaded.owner_handles == ["@alice"]
    assert loaded.tcp_addr == "tcp.example:8090"


def test_named_config_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    assert named_config_path("alice") == tmp_path / "configs" / "alice.toml"
    assert named_config_path("alice.toml") == tmp_path / "configs" / "alice.toml"


def test_resolve_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    assert resolve_config(None) is None
    assert resolve_config("alice") == str(tmp_path / "configs" / "alice.toml")
    assert resolve_config("im.toml") == str(tmp_path / "configs" / "im.toml")
    explicit = str(tmp_path / "custom" / "a.toml")
    assert resolve_config(explicit) == explicit


def test_apply_setting_types() -> None:
    settings = ImSettings()
    assert apply_setting(settings, "model=deepseek-chat") == "model"
    assert settings.model == "deepseek-chat"
    assert apply_setting(settings, "reply_max_chars=1000") == "reply_max_chars"
    assert settings.reply_max_chars == 1000
    assert apply_setting(settings, "ack_preview_chars=20") == "ack_preview_chars"
    assert settings.ack_preview_chars == 20
    assert apply_setting(settings, "owner_handles=@a, @b") == "owner_handles"
    assert settings.owner_handles == ["@a", "@b"]
    assert apply_setting(settings, "allow-tools=read_only") == "allow_tools"
    assert settings.allow_tools == "read_only"


def test_apply_setting_rejects_bad_input() -> None:
    settings = ImSettings()
    for bad in ("model", "unknown=1", "reply_max_chars=abc", "allow_tools=everything"):
        with pytest.raises(ValueError):
            apply_setting(settings, bad)


def test_save_persists_extra_fields(tmp_path: Path) -> None:
    path = tmp_path / "im.toml"
    settings = ImSettings(api_key="sk_live_k", sync_limit=50, provider="deepseek")
    save_im_settings(settings, path)
    loaded = load_im_settings(path, apply_env=False)
    assert loaded.sync_limit == 50
    assert loaded.provider == "deepseek"
