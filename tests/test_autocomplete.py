"""Tests for editor autocomplete logic."""

from __future__ import annotations

from pathlib import Path

from tm.cli.autocomplete import (
    FileIndex,
    apply_completion,
    command_completions,
    detect,
)


def test_detect_command() -> None:
    span = detect("/mo", 3)
    assert span is not None
    assert span.kind == "command"
    assert span.token == "/mo"
    assert (span.start, span.end) == (0, 3)


def test_detect_command_stops_at_space() -> None:
    assert detect("/model gpt", 10) is None


def test_detect_file_reference() -> None:
    text = "read @src/ap"
    span = detect(text, len(text))
    assert span is not None
    assert span.kind == "file"
    assert span.token == "@src/ap"
    assert span.start == 5


def test_detect_ignores_mid_word_at() -> None:
    assert detect("a@b", 3) is None


def test_detect_plain_text() -> None:
    assert detect("hello world", 5) is None


def test_apply_completion() -> None:
    span = detect("read @he", 8)
    assert span is not None
    assert apply_completion("read @he", span, "@hello.txt") == "read @hello.txt "

    cmd = detect("/mo", 3)
    assert cmd is not None
    assert apply_completion("/mo", cmd, "/model") == "/model "


def test_command_completions_ranks_prefix_first() -> None:
    specs = [("model", "switch model"), ("help", "help"), ("new", "new session")]
    results = command_completions("/mo", specs)
    assert results
    assert results[0].value == "/model"
    assert results[0].description == "switch model"


def test_command_completions_empty_prefix_lists_all() -> None:
    specs = [("model", ""), ("help", "")]
    assert {c.value for c in command_completions("/", specs)} == {"/model", "/help"}


def test_file_index_matches(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / "README.md").write_text("x")

    index = FileIndex(tmp_path)
    results = index.match("app")
    assert results
    assert results[0].value == "src/app.py"

    assert index.match("")  # empty prefix returns something
    assert index.match("zzz") == []


def test_file_index_skips_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / "keep.txt").write_text("x")

    files = FileIndex(tmp_path).build()
    assert files == ["keep.txt"]
