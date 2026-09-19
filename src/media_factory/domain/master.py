from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from media_factory.domain.models import MediaInspection


class MasterBuildState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MasterBuild(BaseModel):
    id: str
    package_id: str
    source_asset_id: str
    source_sha256: str
    audio_decision_id: str
    state: MasterBuildState
    output_path: str | None = None
    output_size_bytes: int | None = Field(default=None, ge=0)
    output_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    inspection: MediaInspection | None = None
    ffmpeg_command: list[str] = Field(default_factory=list)
    decode_command: list[str] = Field(default_factory=list)
    video_stream_copy: bool = True
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
