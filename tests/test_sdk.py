from __future__ import annotations

from pathlib import Path

import pytest

from tm.sdk import TmClient, TmError


def _write_config(root: Path) -> None:
    (root / "settings.toml").write_text(
        'provider = "local"\nmodel = "fake"\nenv_probe = false\n', encoding="utf-8"
    )
    (root / "models.toml").write_text(
        '[providers.local]\nname = "Local"\napi = "openai-completions"\n'
        'base_url = "http://127.0.0.1:9/v1"\ndefault_model = "fake"\n\n'
        "[[providers.local.models]]\nid = \"fake\"\ncontext_window = 32768\n"
        "max_tokens = 4096\n",
        encoding="utf-8",
    )


async def test_client_manages_sessions(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    _write_config(config)
    work = tmp_path / "work"
    work.mkdir()

    client = await TmClient.connect(
        cwd=str(work), env={"TM_CONFIG_DIR": str(config)}
    )
    try:
        info = await client.initialize("tests")
        assert info["protocolVersion"] == "1.0"
        assert info["capabilities"]["sessions"] is True
        assert info["session"]["sessionId"]

        session = await client.create_session(access="read-only")
        assert session.id
        detail = await session.info()
        assert detail["access"] == "read-only"
        assert detail["persisted"] is True
        assert detail["cwd"] == str(work)

        listing = await client.sessions()
        ids = [item["sessionId"] for item in listing]
        assert session.id in ids

        assert await session.close() == {"closed": session.id}
        with pytest.raises(TmError) as caught:
            await session.info()
        assert caught.value.code == -32007
    finally:
        await client.close()
    assert client._process.returncode is not None


async def test_client_reports_server_errors(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    _write_config(config)

    client = await TmClient.connect(env={"TM_CONFIG_DIR": str(config)})
    try:
        with pytest.raises(TmError) as caught:
            await client.request("does/not/exist")
        assert caught.value.code == -32601
        assert "does/not/exist" in caught.value.message

        with pytest.raises(TmError) as bad:
            await client.create_session(access="root")
        assert "access must be one of" in bad.value.message

        with pytest.raises(TmError) as tools_error:
            await client.create_session(tools=["not-a-tool"])
        assert tools_error.value.code == -32602
        assert "unknown tool" in tools_error.value.message

        allowed = await client.create_session(tools=["read", "grep"])
        assert (await allowed.info())["tools"] == ["read", "grep"]
    finally:
        await client.close()


async def test_cancel_without_a_turn_is_an_error(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    _write_config(config)
    client = await TmClient.connect(env={"TM_CONFIG_DIR": str(config)})
    try:
        with pytest.raises(TmError) as caught:
            await client.cancel()
        assert caught.value.code == -32002
    finally:
        await client.close()


async def test_model_catalog_and_selection(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    _write_config(config)

    client = await TmClient.connect(env={"TM_CONFIG_DIR": str(config)})
    try:
        providers = await client.request("providers/list")
        entry = next(item for item in providers["providers"] if item["id"] == "local")
        assert entry["hasCredentials"] is True  # localhost needs no key
        assert providers["current"] == {"provider": "local", "id": "fake"}

        models = await client.request("models/list", {"provider": "local"})
        assert [model["id"] for model in models["models"]] == ["fake"]
        assert models["models"][0]["contextWindow"] == 32768

        session = await client.create_session(model="local/fake")
        assert (await session.info())["model"] == {"provider": "local", "id": "fake"}

        with pytest.raises(TmError) as caught:
            await client.request(
                "session/set-model", {"sessionId": session.id, "model": "nope/none"}
            )
        assert caught.value.code == -32008
    finally:
        await client.close()


async def test_startup_without_a_usable_model_lets_the_host_choose(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    _write_config(config)
    (config / "settings.toml").write_text(
        'provider = "nope"\nmodel = "nothing"\n', encoding="utf-8"
    )

    client = await TmClient.connect(env={"TM_CONFIG_DIR": str(config)})
    try:
        info = await client.initialize("tests")
        assert info["session"] is None
        assert info["warnings"], "the unresolved default model must be reported"

        with pytest.raises(TmError) as caught:
            await client.create_session()
        assert caught.value.code == -32008
        assert "no model is configured" in caught.value.message

        session = await client.create_session(model="local/fake")
        assert (await session.info())["model"]["id"] == "fake"
    finally:
        await client.close()


async def test_host_credentials_are_opt_in(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    _write_config(config)
    env = {"TM_CONFIG_DIR": str(config), "DEEPSEEK_API_KEY": ""}

    # Default: the method does not exist.
    client = await TmClient.connect(env=env)
    try:
        info = await client.initialize("tests")
        assert info["capabilities"]["hostCredentials"] is False
        assert "credentials/set" not in info["methods"]
        with pytest.raises(TmError) as off:
            await client.request("credentials/set", {"provider": "deepseek", "apiKey": "x"})
        assert off.value.code == -32601
    finally:
        await client.close()

    # Opt-in: the host can inject and rotate a key at runtime.
    client = await TmClient.connect(env=env, extra_args=["--allow-host-credentials"])
    try:
        info = await client.initialize("tests")
        assert info["capabilities"]["hostCredentials"] is True
        assert "credentials/set" in info["methods"]

        providers = await client.request("providers/list")
        deepseek = next(p for p in providers["providers"] if p["id"] == "deepseek")
        assert deepseek["hasCredentials"] is False

        key = "sk-test-1234567890abcdef"
        result = await client.request(
            "credentials/set", {"provider": "deepseek", "apiKey": key}
        )
        assert result["configured"] is True
        assert result["persisted"] is False
        assert result["masked"].startswith("sk-tes")
        assert key not in _json(result)

        after = await client.request("providers/list")
        deepseek = next(p for p in after["providers"] if p["id"] == "deepseek")
        assert deepseek["hasCredentials"] is True

        cleared = await client.request(
            "credentials/clear", {"provider": "deepseek"}
        )
        assert cleared["configured"] is False

        with pytest.raises(TmError) as unknown:
            await client.request(
                "credentials/set", {"provider": "nope", "apiKey": key}
            )
        assert unknown.value.code == -32602

        with pytest.raises(TmError) as missing:
            await client.request("credentials/set", {"provider": "deepseek"})
        assert "apiKey or keyFile" in missing.value.message

        key_file = tmp_path / "key.txt"
        key_file.write_text("sk-test-from-file\n", encoding="utf-8")
        from_file = await client.request(
            "credentials/set", {"provider": "deepseek", "keyFile": str(key_file)}
        )
        assert from_file["configured"] is True
    finally:
        await client.close()


def _json(value: object) -> str:
    import json

    return json.dumps(value)


async def test_config_dir_flag_isolates_an_instance(tmp_path: Path) -> None:
    tenant = tmp_path / "tenant-42"
    tenant.mkdir()
    _write_config(tenant)

    # No TM_CONFIG_DIR in the environment: the flag alone must place every file
    # (settings, models, sessions, changes) under the host-owned directory.
    client = await TmClient.connect(extra_args=["--config-dir", str(tenant)])
    try:
        info = await client.initialize("tests")
        assert info["session"] is not None
        session = await client.create_session()
        created = await session.info()
        assert Path(created["path"]).parent == (tenant / "sessions").resolve()

        await client.request("session/delete", {"sessionId": session.id})
        assert not Path(created["path"]).exists()
        # The startup session lives in the same tenant directory.
        assert list((tenant / "sessions").glob("*.jsonl"))
    finally:
        await client.close()
