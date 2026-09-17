from __future__ import annotations

from pathlib import Path

from tm.tools.output import format_truncation, spill_output
from tm.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line,
    truncate_tail,
    truncate_text,
)


def numbered(count: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, count + 1))


def test_no_truncation_within_limits() -> None:
    text = numbered(10)
    result = truncate_tail(text)
    assert result.truncated is False
    assert result.truncated_by is None
    assert result.content == text


def test_head_keeps_the_beginning() -> None:
    result = truncate_head(numbered(2500))
    assert result.truncated is True
    assert result.truncated_by == "lines"
    assert result.output_lines == DEFAULT_MAX_LINES
    assert result.content.startswith("line 1\n")
    assert result.content.rstrip().endswith(f"line {DEFAULT_MAX_LINES}")
    assert "line 2500" not in result.content


def test_tail_keeps_the_end() -> None:
    result = truncate_tail(numbered(2500))
    assert result.truncated is True
    assert result.truncated_by == "lines"
    assert result.output_lines == DEFAULT_MAX_LINES
    assert result.content.startswith("line 501\n")
    assert result.content.rstrip().endswith("line 2500")


def test_head_byte_limit_keeps_whole_lines() -> None:
    text = "\n".join("x" * 100 for _ in range(100))
    result = truncate_head(text, max_bytes=250)
    assert result.truncated is True
    assert result.truncated_by == "bytes"
    assert result.output_bytes <= 250
    assert all(line == "x" * 100 for line in result.content.splitlines())


def test_tail_single_huge_line_is_partial() -> None:
    result = truncate_tail("y" * 1000, max_bytes=100)
    assert result.truncated is True
    assert result.truncated_by == "bytes"
    assert result.last_line_partial is True
    assert result.content == "y" * 100


def test_tail_huge_line_does_not_split_a_codepoint() -> None:
    result = truncate_tail("é" * 100, max_bytes=101)
    assert result.last_line_partial is True
    assert result.output_bytes <= 101
    assert result.content == "é" * 50


def test_truncate_text_is_head_compatible() -> None:
    content, truncated = truncate_text(numbered(2500))
    assert truncated is True
    assert content.startswith("line 1\n")
    assert "line 2500" not in content


def test_format_size() -> None:
    assert format_size(500) == "500B"
    assert format_size(2048) == "2.0KB"
    assert format_size(2 * 1024 * 1024) == "2.0MB"


def test_format_truncation_mentions_range_and_path() -> None:
    result = truncate_tail(numbered(2500))
    full_path = Path("/tmp/full.log")
    notice = format_truncation(result, full_path=full_path, tail=True)
    assert "Showing lines 501-2500 of 2500" in notice
    assert str(full_path) in notice


def test_format_truncation_for_head() -> None:
    result = truncate_head(numbered(2500))
    notice = format_truncation(result, tail=False)
    assert notice.startswith("[Showing lines 1-2000 of 2500")


def test_spill_output_writes_the_full_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path / "cfg"))
    path = spill_output("a\nb\n", name="shell")
    assert path is not None
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "a\nb\n"
    assert path.parent == tmp_path / "cfg" / "workspace" / "spill"


def test_spill_output_empty_is_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path / "cfg"))
    assert spill_output("") is None


def test_truncate_line_keeps_the_front() -> None:
    text, truncated = truncate_line("a" * 1000)
    assert truncated is True
    assert text == "a" * GREP_MAX_LINE_LENGTH + "... [truncated]"


def test_truncate_line_short_is_unchanged() -> None:
    assert truncate_line("short") == ("short", False)


def test_default_limits_are_exposed() -> None:
    assert DEFAULT_MAX_LINES == 2000
    assert DEFAULT_MAX_BYTES == 50 * 1024
