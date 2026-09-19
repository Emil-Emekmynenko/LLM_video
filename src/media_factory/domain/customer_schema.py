from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from media_factory.domain.narration import AudioPolicy

MetadataKey = Literal[
    "Title",
    "Description",
    "Publication Date",
    "Category",
    "Duration",
    "Language",
    "Resolution",
    "WPM",
]
FileRole = Literal["master", "transcript", "metadata", "manifest"]


class CustomerSchema(BaseModel):
    customer: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    metadata_key_order: list[MetadataKey]
    required_files: list[FileRole]
    allowed_extensions: list[str]
    category_codes: list[str]
    allowed_audio_policies: list[AudioPolicy]
    allow_synthetic_voice: bool
    require_chapters: bool
    include_manifest_in_delivery: bool
    base_name_template: str
    transcript_duration_tolerance: float = Field(ge=0, le=10)

    @field_validator("allowed_extensions")
    @classmethod
    def normalize_extensions(cls, values: list[str]) -> list[str]:
        return [value.lower() if value.startswith(".") else f".{value.lower()}" for value in values]

    @model_validator(mode="after")
    def validate_complete_schema(self) -> "CustomerSchema":
        expected_keys = {
            "Title",
            "Description",
            "Publication Date",
            "Category",
            "Duration",
            "Language",
            "Resolution",
            "WPM",
        }
        if set(self.metadata_key_order) != expected_keys or len(self.metadata_key_order) != 8:
            raise ValueError("metadata_key_order must contain every supported key exactly once")
        if set(self.required_files) != {"master", "transcript", "metadata", "manifest"}:
            raise ValueError("pilot schema requires master, transcript, metadata, and manifest")
        if not self.category_codes:
            raise ValueError("category_codes cannot be empty")
        if not self.allowed_audio_policies:
            raise ValueError("allowed_audio_policies cannot be empty")
        if self.base_name_template != "{category}_{title}":
            raise ValueError("unsupported base_name_template")
        return self
