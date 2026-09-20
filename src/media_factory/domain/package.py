from datetime import datetime

from pydantic import BaseModel, Field

from media_factory.domain.package_state import PackageState


class PackageCreate(BaseModel):
    source_asset_id: str = Field(min_length=1)


class Package(BaseModel):
    id: str
    source_asset_id: str
    state: PackageState
    version: int
    created_at: datetime
    updated_at: datetime


class PackageTransitionRequest(BaseModel):
    target: PackageState
    expected_version: int = Field(ge=1)
