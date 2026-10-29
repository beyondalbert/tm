from __future__ import annotations

import sys

from tm.terminal import (
    MIN_COLUMNS,
    MIN_ROWS,
    TerminalInfo,
    ensure_utf8,
    format_terminal_info,
    terminal_info,
    tui_problem,
)


def _info(**overrides) -> TerminalInfo:
    values: dict = {
        "term": "xterm-256color",
        "colorterm": "truecolor",
        "no_color": False,
        "is_tty": True,
        "encoding": "utf-8",
        "columns": 120,
        "rows": 40,
        "ssh": False,
        "multiplexer": None,
        "notes": (),
    }
    values.update(overrides)
    return TerminalInfo(**values)


def test_tui_problem_accepts_capable_terminal() -> None:
    assert tui_problem(_info()) is None


def test_tui_problem_rejects_non_tty_and_dumb_term() -> None:
    assert tui_problem(_info(is_tty=False)) is not None
    assert "TERM" in (tui_problem(_info(term="dumb")) or "")
    assert "TERM" in (tui_problem(_info(term="")) or "")


def test_tui_problem_rejects_small_terminal() -> None:
    problem = tui_problem(_info(columns=MIN_COLUMNS - 1, rows=MIN_ROWS))
    assert problem is not None and "small" in problem
    problem = tui_problem(_info(columns=MIN_COLUMNS, rows=MIN_ROWS - 1))
    assert problem is not None and "small" in problem


def test_terminal_info_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 5000 10.0.0.2 22")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("STY", raising=False)

    info = terminal_info()

    assert info.term == "xterm-256color"
    assert info.colorterm == "truecolor"
    assert info.ssh is True
    assert info.multiplexer is None
    assert any("SSH" in note for note in info.notes)


def test_terminal_info_detects_multiplexer(monkeypatch) -> None:
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    info = terminal_info()
    assert info.multiplexer == "tmux"


def test_ensure_utf8_reconfigures_streams(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class Stream:
        def reconfigure(self, *, encoding: str, errors: str) -> None:
            calls.append((encoding, errors))

    monkeypatch.setattr(sys, "stdout", Stream())
    monkeypatch.setattr(sys, "stderr", Stream())

    ensure_utf8()

    assert calls == [("utf-8", "replace"), ("utf-8", "replace")]


def test_format_terminal_info_reports_diagnostics() -> None:
    text = format_terminal_info(_info(term="dumb", notes=("TERM is dumb",)))
    assert "term: dumb" in text
    assert "tui: not usable" in text
    assert "TERM is dumb" in text
    assert "--tui-inline" in text
