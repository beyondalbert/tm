from __future__ import annotations

from tm.cli.main import _mask_key, _resolve_session


def test_mask_key_hides_the_middle() -> None:
    masked = _mask_key("sk-00000000000000000000000000000000")
    assert masked == "sk-000...0000 (35 chars)"
    assert "0000000000" not in masked


def test_mask_key_short_values_are_fully_masked() -> None:
    assert _mask_key("") == "<empty>"
    assert _mask_key("short") == "*****"
    assert _mask_key("0123456789") == "**********"


def _resolve(tmp_path, monkeypatch, **overrides):
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path / "cfg"))
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    monkeypatch.chdir(work)
    kwargs = dict(
        continue_session=False,
        new_session=False,
        resume=False,
        session_path=None,
        no_session=False,
        tui=False,
        all_sessions=False,
    )
    kwargs.update(overrides)
    return _resolve_session([object()], **kwargs)


def test_resolve_session_creates_when_empty(tmp_path, monkeypatch) -> None:
    session, manager, resume_on_start = _resolve(tmp_path, monkeypatch)
    assert session is not None
    assert manager is not None
    assert resume_on_start is False


def test_resolve_session_continues_recent_by_default(tmp_path, monkeypatch) -> None:
    session, _, _ = _resolve(tmp_path, monkeypatch)
    again, _, _ = _resolve(tmp_path, monkeypatch)
    assert again.path == session.path


def test_new_session_flag_starts_fresh(tmp_path, monkeypatch) -> None:
    session, _, _ = _resolve(tmp_path, monkeypatch)
    fresh, _, _ = _resolve(tmp_path, monkeypatch, new_session=True)
    assert fresh.path != session.path


def test_no_session_disables_persistence(tmp_path, monkeypatch) -> None:
    session, manager, resume_on_start = _resolve(tmp_path, monkeypatch, no_session=True)
    assert session is None
    assert manager is None
    assert resume_on_start is False
