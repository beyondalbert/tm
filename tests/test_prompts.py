from __future__ import annotations

from pathlib import Path

from tm.prompts import expand_template, load_prompt_templates, prompt_roots


def test_load_prompt_templates(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "review.md").write_text("Review this: {{input}}")
    (prompts / "standup.md").write_text("Summarize the standup.")
    templates = load_prompt_templates([prompts])
    assert set(templates) == {"review", "standup"}
    assert templates["review"].text == "Review this: {{input}}"


def test_expand_template_with_input_placeholder() -> None:
    assert expand_template("Review this: {{input}}", "src/app.py") == "Review this: src/app.py"
    assert expand_template("Do {{args}}", "it") == "Do it"


def test_expand_template_appends_argument() -> None:
    assert expand_template("Summarize.", "the diff") == "Summarize.\n\nthe diff"
    assert expand_template("Summarize.", "") == "Summarize."


def test_prompt_roots(tmp_path: Path) -> None:
    roots = prompt_roots(tmp_path, tmp_path / "cfg")
    assert roots == [tmp_path / "cfg" / "prompts", tmp_path / ".aiagent" / "prompts"]


def test_project_template_wins(tmp_path: Path) -> None:
    global_root = tmp_path / "cfg" / "prompts"
    project_root = tmp_path / ".aiagent" / "prompts"
    global_root.mkdir(parents=True)
    project_root.mkdir(parents=True)
    (global_root / "x.md").write_text("global")
    (project_root / "x.md").write_text("project")
    templates = load_prompt_templates([global_root, project_root])
    assert templates["x"].text == "project"
