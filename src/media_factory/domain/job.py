from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobKind(StrEnum):
    INSPECT_ASSET = "inspect_asset"
    ANALYZE_VIDEO = "analyze_video"
    GENERATE_NARRATION = "generate_narration"
    BUILD_MASTER = "build_master"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobCreate(BaseModel):
    kind: JobKind
    payload: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    id: str
    package_id: str
    kind: JobKind
    state: JobState
    idempotency_key: str
    payload: dict[str, Any]
    progress: int
    error_code: str | None = None
    error_message: str | None = None
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
