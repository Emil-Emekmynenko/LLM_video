from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import InstrumentedAttribute

from media_factory.domain.metrics import MetricsSummary, StuckJob
from media_factory.persistence.tables import DeliveryAttemptRow, JobRow, PackageRow
from media_factory.services.job_queue import JobQueue


class MetricsService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        queue: JobQueue,
        *,
        stuck_after_seconds: int,
    ) -> None:
        self.session_factory = session_factory
        self.queue = queue
        self.stuck_after_seconds = stuck_after_seconds

    def summary(self) -> MetricsSummary:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.stuck_after_seconds)
        with self.session_factory() as session:
            packages = _group_counts(session, PackageRow.state)
            jobs_by_state = _group_counts(session, JobRow.state)
            jobs_by_kind = _group_counts(session, JobRow.kind)
            deliveries = _group_counts(session, DeliveryAttemptRow.state)
            completed_jobs = list(
                session.scalars(
                    select(JobRow).where(
                        JobRow.started_at.is_not(None),
                        JobRow.finished_at.is_not(None),
                    )
                )
            )
            stuck_rows = list(
                session.scalars(
                    select(JobRow)
                    .where(JobRow.state.in_(("queued", "running")))
                    .where(JobRow.updated_at < cutoff)
                    .order_by(JobRow.updated_at.asc())
                )
            )
        durations: dict[str, list[float]] = {}
        for job in completed_jobs:
            assert job.started_at is not None
            assert job.finished_at is not None
            durations.setdefault(job.kind, []).append(
                (job.finished_at - job.started_at).total_seconds()
            )
        averages = {
            kind: round(sum(values) / len(values), 3) for kind, values in durations.items()
        }
        try:
            queue_depth: int | None = self.queue.depth()
        except Exception:
            queue_depth = None
        return MetricsSummary(
            packages_by_state=packages,
            jobs_by_state=jobs_by_state,
            jobs_by_kind=jobs_by_kind,
            deliveries_by_state=deliveries,
            average_job_seconds_by_kind=averages,
            queue_depth=queue_depth,
            stuck_jobs=[
                StuckJob(
                    id=row.id,
                    package_id=row.package_id,
                    kind=row.kind,
                    state=row.state,
                    updated_at=row.updated_at,
                    age_seconds=round((now - _as_utc(row.updated_at)).total_seconds(), 3),
                )
                for row in stuck_rows
            ],
        )

    def prometheus(self) -> str:
        summary = self.summary()
        lines = [
            "# HELP media_factory_packages Packages by pipeline state.",
            "# TYPE media_factory_packages gauge",
        ]
        _labelled(lines, "media_factory_packages", "state", summary.packages_by_state)
        lines.extend(
            [
                "# HELP media_factory_jobs Jobs by state.",
                "# TYPE media_factory_jobs gauge",
            ]
        )
        _labelled(lines, "media_factory_jobs", "state", summary.jobs_by_state)
        lines.extend(
            [
                "# HELP media_factory_job_duration_seconds Average completed job duration.",
                "# TYPE media_factory_job_duration_seconds gauge",
            ]
        )
        _labelled(
            lines,
            "media_factory_job_duration_seconds",
            "kind",
            summary.average_job_seconds_by_kind,
        )
        lines.extend(
            [
                "# HELP media_factory_stuck_jobs Jobs without updates past the threshold.",
                "# TYPE media_factory_stuck_jobs gauge",
                f"media_factory_stuck_jobs {len(summary.stuck_jobs)}",
            ]
        )
        if summary.queue_depth is not None:
            lines.extend(
                [
                    "# HELP media_factory_queue_depth Jobs currently visible in the queue.",
                    "# TYPE media_factory_queue_depth gauge",
                    f"media_factory_queue_depth {summary.queue_depth}",
                ]
            )
        return "\n".join(lines) + "\n"


def _group_counts(
    session: Session, column: InstrumentedAttribute[Any]
) -> dict[str, int]:
    rows = session.execute(select(column, func.count()).group_by(column))
    return {str(value): int(count) for value, count in rows}


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _labelled(
    lines: list[str], metric: str, label: str, values: Mapping[str, int | float]
) -> None:
    for value, count in sorted(values.items()):
        safe_value = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{metric}{{{label}="{safe_value}"}} {count}')
