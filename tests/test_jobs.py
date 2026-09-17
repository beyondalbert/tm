from __future__ import annotations

from pathlib import Path

from tm.jobs import Job, JobStore, new_job_id


def make_job(
    job_id: str,
    *,
    description: str = "web server",
    command: str = "python -m http.server",
    cwd: str = "/tmp",
    pid: int = 123,
    log: str = "/tmp/web.log",
    started: float = 1.0,
    status: str = "running",
    returncode: int | None = None,
) -> Job:
    return Job(
        id=job_id,
        description=description,
        command=command,
        cwd=cwd,
        pid=pid,
        log=log,
        started=started,
        status=status,
        returncode=returncode,
    )


def test_job_roundtrip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")
    store.save(make_job("a"))
    loaded = store.get("a")
    assert loaded is not None
    assert loaded.command == "python -m http.server"
    assert loaded.status == "running"


def test_job_list_is_sorted_by_start(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")
    store.save(make_job("b", started=2.0))
    store.save(make_job("a", started=1.0))
    assert [job.id for job in store.list()] == ["a", "b"]


def test_job_resolve_by_id_description_and_latest(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")
    store.save(make_job("a", description="first", started=1.0))
    store.save(make_job("b", description="second", started=2.0))
    assert store.resolve("a").id == "a"  # type: ignore[union-attr]
    assert store.resolve("second").id == "b"  # type: ignore[union-attr]
    assert store.resolve(None).id == "b"  # type: ignore[union-attr]
    assert store.resolve("latest").id == "b"  # type: ignore[union-attr]


def test_job_remove(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")
    store.save(make_job("a"))
    store.remove("a")
    assert store.get("a") is None


def test_new_job_id_is_unique() -> None:
    assert new_job_id() != new_job_id()
