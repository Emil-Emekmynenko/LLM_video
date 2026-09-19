from typing import Any
from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.audit import AuditEvent, UserRole
from media_factory.persistence.tables import AuditEventRow
from media_factory.services.request_context import get_request_context


class SQLAlchemyAuditRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str | None = None,
        package_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        context = get_request_context()
        row = AuditEventRow(
            id=str(uuid4()),
            actor=context.principal.actor,
            role=context.principal.role.value,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            package_id=package_id,
            correlation_id=context.correlation_id,
            details=details or {},
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def list_events(
        self,
        *,
        package_id: str | None = None,
        limit: int = 200,
    ) -> list[AuditEvent]:
        statement: Select[tuple[AuditEventRow]] = select(AuditEventRow)
        if package_id is not None:
            statement = statement.where(AuditEventRow.package_id == package_id)
        statement = statement.order_by(AuditEventRow.occurred_at.desc()).limit(limit)
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    @staticmethod
    def _to_domain(row: AuditEventRow) -> AuditEvent:
        return AuditEvent(
            id=row.id,
            occurred_at=row.occurred_at,
            actor=row.actor,
            role=UserRole(row.role),
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            package_id=row.package_id,
            correlation_id=row.correlation_id,
            details=row.details,
        )
