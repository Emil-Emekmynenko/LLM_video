from typing import Any

from media_factory.domain.job import Job, JobKind
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.services.job_queue import JobQueue


class JobDispatchError(RuntimeError):
    pass


class JobService:
    def __init__(self, repository: SQLAlchemyJobRepository, queue: JobQueue) -> None:
        self.repository = repository
        self.queue = queue

    def create(
        self,
        *,
        package_id: str,
        kind: JobKind,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> Job:
        job, created = self.repository.create_or_get(
            package_id=package_id,
            kind=kind,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if created or job.dispatched_at is None:
            try:
                self.queue.enqueue(job.id)
            except Exception as exc:
                raise JobDispatchError("Job was saved but could not be dispatched") from exc
            job = self.repository.mark_dispatched(job.id)
        return job

    def get(self, job_id: str) -> Job:
        return self.repository.get(job_id)

