from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from media_factory.domain.models import Transcript, TranscriptSegment, ValidationIssue


class TranscriptionRunState(StrEnum):
    TRANSCRIBING = "transcribing"
    ALIGNING = "aligning"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ASRResult(BaseModel):
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)


class TranscriptionRun(BaseModel):
    id: str
    package_id: str
    master_build_id: str
    master_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: TranscriptionRunState
    provider_name: str
    provider_version: str
    model_name: str
    model_revision: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    text: str | None = None
    transcript: Transcript | None = None
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
