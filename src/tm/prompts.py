"""Reusable prompt templates: Markdown files invoked as ``/name``.

``{{input}}`` in the template is replaced by the text following the command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PromptTemplate:
    name: str
    path: Path
    text: str


def prompt_roots(cwd: Path, config_dir: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if config_dir is not None:
        roots.append(config_dir / "prompts")
    roots.append(cwd / ".aiagent" / "prompts")
    return roots


def load_prompt_templates(roots: list[Path]) -> dict[str, PromptTemplate]:
    templates: dict[str, PromptTemplate] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            name = path.stem
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            templates[name] = PromptTemplate(name=name, path=path, text=text)
    return templates


def expand_template(text: str, argument: str) -> str:
    argument = argument.strip()
    if "{{input}}" in text:
        return text.replace("{{input}}", argument)
    if "{{args}}" in text:
        return text.replace("{{args}}", argument)
    if argument:
        return f"{text.rstrip()}\n\n{argument}"
    return text


__all__ = [
    "PromptTemplate",
    "expand_template",
    "load_prompt_templates",
    "prompt_roots",
]
