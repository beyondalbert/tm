from __future__ import annotations

import base64
from pathlib import Path

from tm.rpc.changes import collect_changes
from tm.safety import Journal, new_change


def _journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "changes" / "changes.jsonl")


def _file_change(
    journal: Journal,
    *,
    session: str,
    path: Path,
    existed: bool,
    content: bytes | None,
    summary: str,
    ts: float = 100.0,
) -> None:
    journal.record(
        new_change(
            ts=ts,
            tool="edit",
            kind="file",
            target=str(path),
            summary=summary,
            reversible=True,
            session=session,
            undo={
                "path": str(path),
                "existed": existed,
                "content_b64": (
                    base64.b64encode(content).decode("ascii")
                    if content is not None
                    else None
                ),
            },
        )
    )


def test_created_file_reports_added_lines(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("a\nb\n", encoding="utf-8")
    _file_change(journal, session="s1", path=target, existed=False, content=None, summary="create")

    changes = collect_changes(journal, session_id="s1", since_ts=0.0)
    assert len(changes) == 1
    change = changes[0]
    assert change["path"] == str(target)
    assert change["added"] == 2
    assert change["removed"] == 0
    assert change["exists"] is True
    assert "+++ b/app.py" in change["diff"]


def test_modified_file_diffs_against_the_first_change(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("a\nc\n", encoding="utf-8")
    _file_change(
        journal,
        session="s1",
        path=target,
        existed=True,
        content=b"a\nb\n",
        summary="first edit",
        ts=10.0,
    )
    _file_change(
        journal,
        session="s1",
        path=target,
        existed=True,
        content=b"a\nc\n",
        summary="second edit",
        ts=20.0,
    )

    changes = collect_changes(journal, session_id="s1", since_ts=0.0)
    assert len(changes) == 1
    change = changes[0]
    assert change["summary"] == "second edit"
    assert change["added"] == 1
    assert change["removed"] == 1


def test_changes_before_the_turn_are_ignored(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("a\n", encoding="utf-8")
    _file_change(
        journal, session="s1", path=target, existed=False, content=None, summary="old", ts=10.0
    )
    _file_change(
        journal, session="s1", path=target, existed=True, content=b"a\n", summary="new", ts=30.0
    )

    changes = collect_changes(journal, session_id="s1", since_ts=20.0)
    assert [change["summary"] for change in changes] == ["new"]


def test_unknown_previous_content_skips_the_diff(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("a\n", encoding="utf-8")
    _file_change(journal, session="s1", path=target, existed=True, content=None, summary="edit")

    change = collect_changes(journal, session_id="s1", since_ts=0.0)[0]
    assert change["added"] is None
    assert change["diff"] is None


def test_deleted_file_is_reported(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    target = tmp_path / "gone.py"
    _file_change(
        journal, session="s1", path=target, existed=True, content=b"x\ny\n", summary="delete"
    )

    change = collect_changes(journal, session_id="s1", since_ts=0.0)[0]
    assert change["exists"] is False
    assert change["added"] == 0
    assert change["removed"] == 2


def test_other_change_kinds_have_no_diff(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.record(
        new_change(
            ts=5.0,
            tool="package",
            kind="package",
            target="jq",
            summary="installed jq",
            reversible=True,
            session="s1",
        )
    )
    change = collect_changes(journal, session_id="s1", since_ts=0.0)[0]
    assert change["kind"] == "package"
    assert change["summary"] == "installed jq"
    assert "diff" not in change


def test_any_session_includes_untagged_changes(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("a\n", encoding="utf-8")
    _file_change(journal, session="", path=target, existed=False, content=None, summary="create")

    assert collect_changes(journal, session_id="mem-1", since_ts=0.0) == []
    assert len(collect_changes(journal, session_id="mem-1", since_ts=0.0, any_session=True)) == 1
