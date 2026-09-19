from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    OPERATOR = "operator"
    QA = "qa"
    ADMIN = "admin"
    SERVICE = "service"


class Principal(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    role: UserRole


class AuditEvent(BaseModel):
    id: str
    occurred_at: datetime
    actor: str
    role: UserRole
    action: str
    entity_type: str
    entity_id: str | None = None
    package_id: str | None = None
    correlation_id: str
    details: dict[str, Any] = Field(default_factory=dict)
