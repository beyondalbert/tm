"""Agent Skills: on-demand capability packages defined by ``SKILL.md`` files.

A skill directory contains ``SKILL.md`` with optional YAML-ish frontmatter:

    ---
    name: code-review
    description: Review code for bugs and security issues
    ---
    Steps...
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    _, frontmatter, body = parts
    meta: dict[str, str] = {}
    for line in frontmatter.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body


def skill_roots(
    cwd: Path, config_dir: Path | None = None, *, include_project: bool = True
) -> list[Path]:
    roots: list[Path] = []
    if config_dir is not None:
        roots.append(config_dir / "skills")
    if include_project:
        roots.append(cwd / ".agents" / "skills")
        roots.append(cwd / ".aiagent" / "skills")
    return roots


def load_skills(roots: list[Path]) -> list[Skill]:
    skills: dict[str, Skill] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, body = parse_frontmatter(text)
            name = meta.get("name") or skill_md.parent.name
            skills[name] = Skill(
                name=name,
                description=meta.get("description", ""),
                path=skill_md,
                body=body.strip(),
            )
    return list(skills.values())


def build_skills_section(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = ["Available skills (load one with /skill:<name>):"]
    for skill in skills:
        suffix = f": {skill.description}" if skill.description else ""
        lines.append(f"- {skill.name}{suffix}")
    return "\n".join(lines)


def find_skill(skills: list[Skill], name: str) -> Skill | None:
    for skill in skills:
        if skill.name == name:
            return skill
    return None


__all__ = [
    "Skill",
    "build_skills_section",
    "find_skill",
    "load_skills",
    "parse_frontmatter",
    "skill_roots",
]
