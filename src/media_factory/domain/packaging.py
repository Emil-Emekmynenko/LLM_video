from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from media_factory.domain.models import ValidationIssue


class PackageBuildState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DeliveryMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(alias="Title")
    description: str = Field(alias="Description")
    publication_date: date | None = Field(default=None, alias="Publication Date")
    category: str = Field(alias="Category")
    duration: float = Field(alias="Duration", ge=0)
    language: str | None = Field(default=None, alias="Language")
    resolution: str = Field(alias="Resolution")
    wpm: float = Field(alias="WPM", ge=0)


class PackageFile(BaseModel):
    role: str
    filename: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str
    local_relative_path: str
    target_relative_path: str


class PackageBuild(BaseModel):
    id: str
    package_id: str
    version: int = Field(ge=1)
    state: PackageBuildState
    master_build_id: str
    transcription_run_id: str
    metadata_version_id: str
    delivery_id: str
    customer: str
    schema_version: str
    base_name: str
    output_dir: str | None = None
    files: list[PackageFile] = Field(default_factory=list)
    manifest_path: str | None = None
    manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    computed_metadata: DeliveryMetadata | None = None
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    build_parameters: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
