from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import BaseModel, Field, field_validator


class DeliveryState(StrEnum):
    QUEUED = "queued"
    UPLOADING_MEDIA = "uploading_media"
    UPLOADING_SIDECARS = "uploading_sidecars"
    VERIFYING = "verifying"
    FAILED = "failed"
    COMPLETE = "complete"


class DeliveryCreateRequest(BaseModel):
    package_build_id: str = Field(min_length=1)
    prefix: str = ""

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        normalized = value.strip("/")
        path = PurePosixPath(normalized)
        if normalized and (path.is_absolute() or ".." in path.parts or "\\" in normalized):
            raise ValueError("prefix must be a safe relative object path")
        return normalized


class DeliveryAttempt(BaseModel):
    id: str
    package_id: str
    package_build_id: str
    provider: str
    destination: str
    prefix: str
    state: DeliveryState
    idempotency_key: str
    delivered_at: datetime
    media_uploaded_at: datetime | None = None
    sidecars_uploaded_at: datetime | None = None
    package_complete_at: datetime | None = None
    delivery_failed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class UploadedObject(BaseModel):
    id: str
    delivery_attempt_id: str
    role: str
    local_relative_path: str
    remote_key: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_checksum: str | None = None
    uploaded_at: datetime
    verified_at: datetime | None = None
