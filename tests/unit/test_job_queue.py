from typing import Any

from media_factory.services import job_queue


class RecordingQueue:
    def __init__(self, name: str, connection: object) -> None:
        self.name = name
        self.connection = connection
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


def test_redis_queue_applies_configured_job_timeout(monkeypatch: Any) -> None:
    monkeypatch.setattr(job_queue, "Queue", RecordingQueue)

    queue = job_queue.RedisJobQueue(
        "redis://localhost:6379/0",
        "media-tasks",
        job_timeout_seconds=1800,
    )
    queue.enqueue("job-123")

    args, kwargs = queue.queue.calls[0]
    assert args == ("media_factory.workers.tasks.execute_job", "job-123")
    assert kwargs["job_timeout"] == 1800
