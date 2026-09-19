from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class NarrationScriptStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class TTSRunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AudioPolicy(StrEnum):
    PRESERVE = "preserve"
    REMOVE = "remove"
    REPLACE = "replace"
    ADDITIONAL = "additional"


class NarrationScriptContent(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    language: str = Field(min_length=2, max_length=35)
    style: str = Field(min_length=1, max_length=200)
    target_wpm: int = Field(ge=60, le=240)


class NarrationScript(NarrationScriptContent):
    id: str
    package_id: str
    metadata_version_id: str
    version: int = Field(ge=1)
    status: NarrationScriptStatus
    created_by: str
    change_note: str | None = None
    created_at: datetime
    approved_at: datetime | None = None


class NarrationProposalRequest(BaseModel):
    metadata_version_id: str = Field(min_length=1)
    style: str = Field(default="neutral, factual", min_length=1, max_length=200)
    target_wpm: int = Field(default=130, ge=60, le=240)


class NarrationRevisionRequest(NarrationScriptContent):
    base_version: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=200)
    change_note: str | None = Field(default=None, max_length=1000)


class NarrationApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)


class TTSRun(BaseModel):
    id: str
    package_id: str
    script_id: str
    state: TTSRunState
    provider_name: str
    provider_version: str
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class AudioTrack(BaseModel):
    id: str
    package_id: str
    tts_run_id: str
    script_id: str
    path: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration: float = Field(gt=0)
    language: str
    synthetic: bool
    created_at: datetime


class AudioDecision(BaseModel):
    id: str
    package_id: str
    version: int = Field(ge=1)
    policy: AudioPolicy
    audio_track_id: str | None = None
    created_by: str
    created_at: datetime


class AudioDecisionRequest(BaseModel):
    policy: AudioPolicy
    audio_track_id: str | None = None
    created_by: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_track_selection(self) -> "AudioDecisionRequest":
        requires_track = self.policy in {AudioPolicy.REPLACE, AudioPolicy.ADDITIONAL}
        if requires_track != (self.audio_track_id is not None):
            raise ValueError("selected audio policy and audio track do not match")
        return self
