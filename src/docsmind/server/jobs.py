"""In-process background jobs for embedding/captioning runs.

Jobs live in memory in the server process: fine for a single-process local
app, and they are safe to lose because the work itself is resumable (a restart
just means the user clicks "Proceed" again and it continues where it stopped).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from docsmind.enrichment.service import EnrichmentService

log = logging.getLogger(__name__)

_KEEP_FINISHED = 20


class JobConflictError(Exception):
    """A job of this kind is already running."""


@dataclass
class Job:
    kind: str
    version_ids: list[UUID] | None
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    status: str = "queued"  # queued | running | done | failed | cancelled
    done: int = 0
    total: int = 0
    message: str = ""
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "version_ids": [str(v) for v in self.version_ids] if self.version_ids is not None else None,
            "cancelling": self.active and self.cancel_event.is_set(),
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def latest(self, kind: str) -> Job | None:
        """The running job of this kind, else the most recent finished one."""
        jobs = [j for j in self._jobs.values() if j.kind == kind]
        if not jobs:
            return None
        active = [j for j in jobs if j.active]
        return active[-1] if active else max(jobs, key=lambda j: j.created_at)

    def start(self, kind: str, service: EnrichmentService, version_ids: list[UUID] | None) -> Job:
        running = self.latest(kind)
        if running is not None and running.active:
            raise JobConflictError(f"{kind} is already running.")
        job = Job(kind=kind, version_ids=version_ids)
        self._jobs[job.id] = job
        self._trim()
        task = asyncio.create_task(self._run(job, service))
        self._tasks.add(task)  # keep a reference so it isn't garbage-collected mid-run
        task.add_done_callback(self._tasks.discard)
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job is not None and job.active:
            job.cancel_event.set()
        return job

    async def _run(self, job: Job, service: EnrichmentService) -> None:
        job.status = "running"

        def on_progress(done: int, total: int, message: str) -> None:
            job.done, job.total, job.message = done, total, message

        try:
            result = await service.run(
                job.kind, job.version_ids, on_progress=on_progress, cancel=job.cancel_event
            )
            job.result = result
            job.status = "cancelled" if result.get("cancelled") else "done"
            if result.get("failed"):
                job.message = f"{result['failed']} failed; run again to retry them."
        except Exception as exc:
            log.exception("%s job failed", job.kind)
            job.status = "failed"
            job.error = str(exc) or type(exc).__name__
        finally:
            job.finished_at = time.time()

    def _trim(self) -> None:
        finished = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.created_at)
        for old in finished[:-_KEEP_FINISHED]:
            self._jobs.pop(old.id, None)
