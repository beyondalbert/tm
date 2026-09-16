from __future__ import annotations

from pathlib import Path

from tm.skills import build_skills_section, find_skill, load_skills, parse_frontmatter, skill_roots


def test_parse_frontmatter() -> None:
    meta, body = parse_frontmatter("---\nname: x\ndescription: does x\n---\nBody here")
    assert meta == {"name": "x", "description": "does x"}
    assert body.strip() == "Body here"


def test_parse_frontmatter_without_header() -> None:
    meta, body = parse_frontmatter("just body")
    assert meta == {}
    assert body == "just body"


def write_skill(root: Path, dirname: str, text: str) -> None:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(text)


def test_load_skills_from_multiple_roots(tmp_path: Path) -> None:
    global_root = tmp_path / "config" / "skills"
    project_root = tmp_path / ".aiagent" / "skills"
    write_skill(global_root, "review", "---\nname: review\ndescription: review code\n---\nSteps")
    write_skill(project_root, "deploy", "# Deploy\nDo the deploy.")

    skills = load_skills([global_root, project_root])
    names = {skill.name for skill in skills}
    assert names == {"review", "deploy"}
    deploy = find_skill(skills, "deploy")
    assert deploy is not None and deploy.description == ""
    section = build_skills_section(skills)
    assert "review: review code" in section


def test_project_skill_wins_over_global(tmp_path: Path) -> None:
    global_root = tmp_path / "config" / "skills"
    project_root = tmp_path / ".aiagent" / "skills"
    write_skill(global_root, "dup", "---\nname: shared\ndescription: global\n---\ng")
    write_skill(project_root, "dup", "---\nname: shared\ndescription: project\n---\np")

    skills = load_skills([global_root, project_root])
    assert len(skills) == 1
    assert skills[0].description == "project"


def test_skill_roots(tmp_path: Path) -> None:
    roots = skill_roots(tmp_path, tmp_path / "cfg")
    assert roots == [
        tmp_path / "cfg" / "skills",
        tmp_path / ".agents" / "skills",
        tmp_path / ".aiagent" / "skills",
    ]


def test_build_skills_section_empty() -> None:
    assert build_skills_section([]) == ""
