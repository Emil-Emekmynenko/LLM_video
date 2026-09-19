from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class QADecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class QAReviewRequest(BaseModel):
    package_build_id: str = Field(min_length=1)
    approved: bool
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> "QAReviewRequest":
        if not self.approved and not (self.reason or "").strip():
            raise ValueError("rejection reason is required")
        return self


class QAReview(BaseModel):
    id: str
    package_id: str
    package_build_id: str
    decision: QADecision
    reviewer: str
    reason: str | None = None
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_at: datetime


class ExportState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LocalExport(BaseModel):
    id: str
    package_id: str
    package_build_id: str
    qa_review_id: str
    state: ExportState
    archive_path: str | None = None
    archive_size_bytes: int | None = Field(default=None, ge=0)
    archive_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
