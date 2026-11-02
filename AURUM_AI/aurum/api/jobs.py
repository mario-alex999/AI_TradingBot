"""In-process background job runner for long COT operations (backfill / update / calculate).

Jobs run on a single worker thread, so ingestion jobs never overlap (the database upserts are idempotent
anyway). Job state lives in memory: it is lost on API restart, but every run is also recorded durably in
``cot_ingestion_logs``.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    id: str
    type: str
    params: dict[str, Any]
    status: str = "queued"                  # queued | running | succeeded | failed
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None

    def public(self) -> dict[str, Any]:
        return {"jobId": self.id, "type": self.type, "status": self.status, "params": self.params,
                "createdAt": self.created_at, "startedAt": self.started_at, "finishedAt": self.finished_at,
                "progress": self.progress[-50:], "result": self.result, "error": self.error}


class JobManager:
    def __init__(self, max_history: int = 100):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aurum-job")
        self._max = max_history

    def submit(self, job_type: str, params: dict[str, Any], fn: Callable[[Callable[[str], None]], dict[str, Any]]) -> Job:
        job = Job(str(uuid.uuid4()), job_type, params)
        with self._lock:
            self._jobs[job.id] = job
            if len(self._jobs) > self._max:
                for old in sorted(self._jobs.values(), key=lambda j: j.created_at)[: len(self._jobs) - self._max]:
                    if old.status in ("succeeded", "failed"):
                        self._jobs.pop(old.id, None)

        def run() -> None:
            job.status, job.started_at = "running", _now()
            try:
                job.result = fn(lambda msg: job.progress.append(msg))
                job.status = "succeeded"
            except Exception as exc:  # noqa: BLE001 - surfaced through the job record
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
                job.progress.append(traceback.format_exc(limit=3))
            finally:
                job.finished_at = _now()

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
