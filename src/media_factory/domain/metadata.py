from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class MetadataStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class MetadataChapter(BaseModel):
    start: float = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)


class MetadataContent(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    category: str = Field(min_length=1, max_length=100)
    chapters: list[MetadataChapter] = Field(default_factory=list)
    narration_language: str | None = Field(default=None, min_length=2, max_length=35)

    @model_validator(mode="after")
    def validate_chapter_order(self) -> "MetadataContent":
        starts = [chapter.start for chapter in self.chapters]
        if starts != sorted(starts) or len(starts) != len(set(starts)):
            raise ValueError("chapter timestamps must be unique and sorted")
        return self


class MetadataVersion(MetadataContent):
    id: str
    package_id: str
    analysis_run_id: str
    version: int = Field(ge=1)
    status: MetadataStatus
    created_by: str
    change_note: str | None = None
    created_at: datetime
    approved_at: datetime | None = None


class MetadataProposalRequest(BaseModel):
    analysis_run_id: str = Field(min_length=1)


class MetadataRevisionRequest(MetadataContent):
    base_version: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=200)
    change_note: str | None = Field(default=None, max_length=1000)


class MetadataApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)


class MetadataCategory(BaseModel):
    code: str
    label: str
