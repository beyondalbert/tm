"""Tests for the project trust gate."""

from __future__ import annotations

from pathlib import Path

from tm.cli.main import _resolve_trust
from tm.config import Settings
from tm.extensions import extension_roots
from tm.permissions.policy import Policy
from tm.prompts import prompt_roots
from tm.skills import skill_roots
from tm.trust import TrustManager, project_resources


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / ".aiagent" / "extensions").mkdir(parents=True)
    (tmp_path / ".aiagent" / "extensions" / "x.py").write_text("def setup(api): pass\n")
    return tmp_path


def test_bare_aiagent_dir_does_not_require_trust(tmp_path: Path) -> None:
    (tmp_path / ".aiagent").mkdir()
    assert project_resources(tmp_path) == []


def test_project_resources_are_detected(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / ".aiagent" / "policy.toml").write_text('default = "allow"\n')
    found = {path.name for path in project_resources(tmp_path)}
    assert "extensions" in found
    assert "policy.toml" in found


def test_trust_manager_saves_and_matches_parents(tmp_path: Path) -> None:
    manager = TrustManager(tmp_path / "trust.json")
    work = tmp_path / "work"
    work.mkdir()
    assert manager.decision(work) is None

    manager.save(work, True)
    assert manager.decision(work) is True
    nested = work / "a" / "b"
    assert manager.decision(nested) is True

    manager.save(nested, False)
    assert manager.decision(nested) is False
    assert manager.decision(work) is True


def test_disable_project_roots(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    assert skill_roots(tmp_path, cfg, include_project=False) == [cfg / "skills"]
    assert prompt_roots(tmp_path, cfg, include_project=False) == [cfg / "prompts"]
    assert extension_roots(tmp_path, cfg, include_project=False) == [cfg / "extensions"]


def test_policy_ignores_project_rules_when_untrusted(tmp_path: Path) -> None:
    project = tmp_path / ".aiagent"
    project.mkdir()
    (project / "policy.toml").write_text('default = "allow"\n')

    trusted = Policy.load(cwd=tmp_path, config_dir=tmp_path / "cfg")
    assert trusted.default.value == "allow"

    untrusted = Policy.load(cwd=tmp_path, config_dir=tmp_path / "cfg", include_project=False)
    assert untrusted.default.value == "ask"


def test_resolve_trust_no_resources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _resolve_trust(Settings(), approve=False, no_approve=False) is True


def test_resolve_trust_cli_overrides(tmp_path: Path, monkeypatch) -> None:
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _resolve_trust(Settings(), approve=True, no_approve=False) is True
    assert _resolve_trust(Settings(), approve=False, no_approve=True) is False


def test_resolve_trust_saved_and_default(tmp_path: Path, monkeypatch) -> None:
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path / "cfg"))

    assert _resolve_trust(Settings(), approve=False, no_approve=False) is False  # no tty

    assert _resolve_trust(Settings(default_project_trust="always"), False, False) is True
    assert _resolve_trust(Settings(default_project_trust="never"), False, False) is False

    TrustManager(tmp_path / "cfg" / "trust.json").save(tmp_path, True)
    assert _resolve_trust(Settings(default_project_trust="never"), False, False) is True
