from __future__ import annotations

from pathlib import Path

from tm.im.state import ImState


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = ImState.load(path)
    state.set_last_seq(1001, 7)
    state.set_session_path(1001, tmp_path / "s.jsonl")
    state.save()

    loaded = ImState.load(path)
    assert loaded.last_seq(1001) == 7
    assert loaded.session_path(1001) == tmp_path / "s.jsonl"


def test_last_seq_is_monotonic(tmp_path: Path) -> None:
    state = ImState(tmp_path / "state.json")
    state.set_last_seq(1, 5)
    state.set_last_seq(1, 3)
    assert state.last_seq(1) == 5


def test_set_cursors_merges(tmp_path: Path) -> None:
    state = ImState(tmp_path / "state.json")
    state.set_cursors({1: 2, 2: 4})
    state.set_cursors({2: 6, 3: 1})
    assert state.cursors == {1: 2, 2: 6, 3: 1}


def test_load_missing_file(tmp_path: Path) -> None:
    assert ImState.load(tmp_path / "nope.json").cursors == {}


def test_load_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert ImState.load(path).cursors == {}
