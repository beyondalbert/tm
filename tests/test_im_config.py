from __future__ import annotations

from pathlib import Path

from tm.im.config import (
    ImSettings,
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
    assert load_im_settings().api_key == "env"


def test_unknown_choice_falls_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TM_IM_ALLOW_TOOLS", raising=False)
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "im.toml").write_text('allow_tools = "everything"\n', encoding="utf-8")
    assert load_im_settings().allow_tools == "full"


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
