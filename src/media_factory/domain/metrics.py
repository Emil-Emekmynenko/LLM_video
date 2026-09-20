from datetime import datetime

from pydantic import BaseModel


class StuckJob(BaseModel):
    id: str
    package_id: str
    kind: str
    state: str
    updated_at: datetime
    age_seconds: float


class MetricsSummary(BaseModel):
    packages_by_state: dict[str, int]
    jobs_by_state: dict[str, int]
    jobs_by_kind: dict[str, int]
    deliveries_by_state: dict[str, int]
    average_job_seconds_by_kind: dict[str, float]
    queue_depth: int | None
    stuck_jobs: list[StuckJob]
