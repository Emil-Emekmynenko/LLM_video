from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class AnalysisRunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ClipInterval(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "ClipInterval":
        if self.end <= self.start:
            raise ValueError("clip end must be greater than start")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class DetectedEvent(BaseModel):
    relative_start: float = Field(ge=0)
    relative_end: float = Field(ge=0)
    actor: str
    action: str
    objects: list[str] = Field(default_factory=list)
    evidence: str
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> "DetectedEvent":
        if self.relative_end < self.relative_start:
            raise ValueError("event end must not be before start")
        return self


class ClipAnalysis(BaseModel):
    summary: str
    participants: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    events: list[DetectedEvent] = Field(default_factory=list)
    uncertainty: str | None = None


class AnalysisClip(BaseModel):
    id: str
    analysis_run_id: str
    interval: ClipInterval
    clip_path: str
    result: ClipAnalysis
    created_at: datetime


class AnalysisRun(BaseModel):
    id: str
    package_id: str
    source_sha256: str
    state: AnalysisRunState
    provider_name: str
    provider_version: str
    prompt_version: str
    proxy_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

