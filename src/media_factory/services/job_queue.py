from typing import Protocol

from redis import Redis
from rq import Queue


class JobQueue(Protocol):
    def enqueue(self, job_id: str) -> None: ...

    def is_available(self) -> bool: ...

    def depth(self) -> int: ...


class RedisJobQueue:
    def __init__(self, redis_url: str, queue_name: str) -> None:
        self.connection = Redis.from_url(redis_url)
        self.queue = Queue(queue_name, connection=self.connection)

    def enqueue(self, job_id: str) -> None:
        self.queue.enqueue(
            "media_factory.workers.tasks.execute_job",
            job_id,
            job_id=job_id,
            result_ttl=86400,
            failure_ttl=604800,
        )

    def is_available(self) -> bool:
        try:
            return bool(self.connection.ping())
        except Exception:
            return False

    def depth(self) -> int:
        return len(self.queue)


class InMemoryJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)

    def is_available(self) -> bool:
        return True

    def depth(self) -> int:
        return len(self.enqueued)
