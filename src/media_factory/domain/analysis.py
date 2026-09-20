from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

ShortLabel = Annotated[str, Field(min_length=1, max_length=100)]


class AnalysisRunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalysisReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


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
    actor: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=160)
    objects: list[ShortLabel] = Field(default_factory=list, max_length=10)
    evidence: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> "DetectedEvent":
        if self.relative_end < self.relative_start:
            raise ValueError("event end must not be before start")
        return self


class SuggestedChapter(BaseModel):
    relative_start: float = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    evidence: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)


class ClipAnalysis(BaseModel):
    summary: str = Field(min_length=1, max_length=300)
    participants: list[ShortLabel] = Field(default_factory=list, max_length=10)
    objects: list[ShortLabel] = Field(default_factory=list, max_length=10)
    events: list[DetectedEvent] = Field(default_factory=list, max_length=4)
    suggested_chapters: list[SuggestedChapter] = Field(default_factory=list, max_length=4)
    uncertainty: str | None = Field(default=None, max_length=300)


class AnalysisClip(BaseModel):
    id: str
    analysis_run_id: str
    interval: ClipInterval
    clip_path: str
    result: ClipAnalysis
    inference_seconds: float | None = None
    created_at: datetime


class TimelineEvent(BaseModel):
    id: str
    analysis_run_id: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    actor: str
    action: str
    objects: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    source_clip_ids: list[str] = Field(default_factory=list)
    conflict_event_ids: list[str] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.PENDING
    version: int = Field(default=1, ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_order(self) -> "TimelineEvent":
        if self.end < self.start:
            raise ValueError("event end must not be before start")
        return self


class AnalysisRun(BaseModel):
    id: str
    package_id: str
    source_sha256: str
    state: AnalysisRunState
    review_status: AnalysisReviewStatus = AnalysisReviewStatus.PENDING
    provider_name: str
    provider_version: str
    prompt_version: str
    inference_parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    proxy_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    reviewed_at: datetime | None = None


class EventReviewRequest(BaseModel):
    review_status: ReviewStatus
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_final_status(self) -> "EventReviewRequest":
        if self.review_status not in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
            raise ValueError("event review must approve or reject the event")
        return self


class AnalysisReviewRequest(BaseModel):
    approved: bool
