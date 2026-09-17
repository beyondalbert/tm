"""Background job records for the process tool."""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Job:
    id: str
    description: str
    command: str
    cwd: str
    pid: int
    log: str
    started: float
    status: str = "running"
    returncode: int | None = None


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _from_dict(data: dict) -> Job:
    returncode = data.get("returncode")
    return Job(
        id=str(data.get("id", "")),
        description=str(data.get("description", "")),
        command=str(data.get("command", "")),
        cwd=str(data.get("cwd", "")),
        pid=int(data.get("pid", 0)),
        log=str(data.get("log", "")),
        started=float(data.get("started", 0.0)),
        status=str(data.get("status", "running")),
        returncode=int(returncode) if isinstance(returncode, int) else None,
    )


class JobStore:
    """One JSON file per job under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def save(self, job: Job) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(job.id).write_text(
            json.dumps(asdict(job), indent=2), encoding="utf-8"
        )

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return _from_dict(data)

    def list(self) -> list[Job]:
        if not self.root.is_dir():
            return []
        jobs = [job for path in self.root.glob("*.json") if (job := self.get(path.stem))]
        return sorted(jobs, key=lambda job: job.started)

    def resolve(self, ref: str | None) -> Job | None:
        """Find a job by id, exact description, or ``latest``/empty."""
        if ref is None or ref == "latest":
            jobs = self.list()
            return jobs[-1] if jobs else None
        job = self.get(ref)
        if job is not None:
            return job
        for candidate in self.list():
            if candidate.description == ref:
                return candidate
        return None

    def remove(self, job_id: str) -> None:
        with contextlib.suppress(OSError):
            self._path(job_id).unlink()


__all__ = ["Job", "JobStore", "new_job_id"]
