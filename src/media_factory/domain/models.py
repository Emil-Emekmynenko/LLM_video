from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    code: str
    severity: Severity = Severity.ERROR
    path: str
    message: str
    blocking: bool = True


class TranscriptWord(BaseModel):
    word: str
    start: float
    end: float


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)


class Transcript(BaseModel):
    schema_version: str = "1.0"
    asset_id: str
    master_sha256: str
    language: str | None = None
    media_duration: float
    segments: list[TranscriptSegment] = Field(default_factory=list)


class StreamInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int
    codec_type: str
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    avg_frame_rate: str | None = None


class MediaInspection(BaseModel):
    duration: float
    size_bytes: int
    format_name: str
    streams: list[StreamInfo]
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @property
    def video_streams(self) -> list[StreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "video"]

    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "audio"]


class StoredAsset(BaseModel):
    id: str
    original_name: str
    stored_path: Path = Field(exclude=True)
    size_bytes: int
    sha256: str
    duplicate_of: str | None = None
    inspection: MediaInspection | None = None
