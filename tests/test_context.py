from __future__ import annotations

from pathlib import Path

from tm.context.agents_md import build_context_section, load_context_files


def test_loads_agents_md_from_cwd(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("project rules")
    files = load_context_files(tmp_path)
    names = [path.name for path, _ in files]
    assert "AGENTS.md" in names
    assert "project rules" in build_context_section(files)


def test_override_wins_over_agents(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("base")
    (tmp_path / "AGENTS.override.md").write_text("override")
    files = load_context_files(tmp_path)
    texts = [text for _, text in files]
    assert "override" in texts
    assert "base" not in texts


def test_global_and_parent_files_are_included(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("root rules")
    (child / "AGENTS.md").write_text("child rules")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "AGENTS.md").write_text("global rules")

    files = load_context_files(child, global_dir)
    texts = [text for _, text in files]
    assert texts == ["global rules", "root rules", "child rules"]


def test_missing_files_returns_empty(tmp_path: Path) -> None:
    files = load_context_files(tmp_path)
    assert files == []
    assert build_context_section(files) == ""
