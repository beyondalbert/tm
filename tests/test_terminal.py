from __future__ import annotations

import os
import sys

from tm.terminal import (
    MIN_COLUMNS,
    MIN_ROWS,
    TerminalInfo,
    ensure_utf8,
    format_terminal_info,
    preferred_color_system,
    prepare_tui_env,
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


def _clear_color_env(monkeypatch) -> None:
    for name in ("TERM", "COLORTERM", "TEXTUAL_COLOR_SYSTEM", "TM_TUI_COLOR_SYSTEM"):
        monkeypatch.delenv(name, raising=False)


def test_preferred_color_system_upgrades_plain_xterm(monkeypatch) -> None:
    _clear_color_env(monkeypatch)
    monkeypatch.setenv("TERM", "xterm")

    assert preferred_color_system() == "256"


def test_preferred_color_system_leaves_256color_alone(monkeypatch) -> None:
    _clear_color_env(monkeypatch)
    monkeypatch.setenv("TERM", "xterm-256color")

    assert preferred_color_system() is None


def test_preferred_color_system_respects_truecolor_and_overrides(monkeypatch) -> None:
    _clear_color_env(monkeypatch)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert preferred_color_system() is None

    monkeypatch.delenv("COLORTERM")
    monkeypatch.setenv("TM_TUI_COLOR_SYSTEM", "16")
    assert preferred_color_system() == "standard"

    monkeypatch.setenv("TM_TUI_COLOR_SYSTEM", "auto")
    assert preferred_color_system() == "256"

    monkeypatch.setenv("TEXTUAL_COLOR_SYSTEM", "standard")
    assert preferred_color_system() is None


def test_prepare_tui_env_sets_textual_color_system(monkeypatch) -> None:
    _clear_color_env(monkeypatch)
    monkeypatch.setenv("TERM", "xterm")

    assert prepare_tui_env() == "256"
    assert os.environ["TEXTUAL_COLOR_SYSTEM"] == "256"


def test_format_terminal_info_reports_color_system(monkeypatch) -> None:
    _clear_color_env(monkeypatch)
    monkeypatch.setenv("TERM", "xterm")

    text = format_terminal_info(_info(term="xterm"))

    assert "color_system: 256" in text
