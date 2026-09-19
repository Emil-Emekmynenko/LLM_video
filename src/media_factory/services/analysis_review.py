from media_factory.domain.analysis import (
    AnalysisReviewStatus,
    AnalysisRun,
    AnalysisRunState,
    ReviewStatus,
    TimelineEvent,
)
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository


class AnalysisReviewError(RuntimeError):
    code = "analysis_review_blocked"


class AnalysisReviewService:
    def __init__(
        self,
        repository: SQLAlchemyAnalysisRepository,
        packages: SQLAlchemyPackageRepository,
    ) -> None:
        self.repository = repository
        self.packages = packages

    def review_event(
        self,
        event_id: str,
        *,
        review_status: ReviewStatus,
        expected_version: int,
    ) -> TimelineEvent:
        run = self.repository.get(self.repository.get_event(event_id).analysis_run_id)
        if run.state is not AnalysisRunState.SUCCEEDED:
            raise AnalysisReviewError("analysis run has not succeeded")
        if run.review_status is not AnalysisReviewStatus.PENDING:
            raise AnalysisReviewError("reviewed analysis runs are immutable")
        return self.repository.review_event(
            event_id,
            review_status=review_status,
            expected_version=expected_version,
        )

    def review_run(self, run_id: str, *, approved: bool) -> AnalysisRun:
        run = self.repository.get(run_id)
        if run.state is not AnalysisRunState.SUCCEEDED:
            raise AnalysisReviewError("analysis run has not succeeded")
        if run.review_status is not AnalysisReviewStatus.PENDING:
            raise AnalysisReviewError("analysis run has already been reviewed")
        if approved:
            events = self.repository.list_events(run_id)
            unresolved = [
                event.id
                for event in events
                if event.review_status not in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}
            ]
            if unresolved:
                raise AnalysisReviewError(
                    f"analysis has {len(unresolved)} unresolved events"
                )
            status = AnalysisReviewStatus.APPROVED
        else:
            status = AnalysisReviewStatus.REJECTED
        reviewed = self.repository.set_review_status(run_id, status)
        if not approved:
            package = self.packages.get(run.package_id)
            if package.state is PackageState.AWAITING_METADATA_REVIEW:
                self.packages.transition(
                    package.id,
                    target=PackageState.READY_FOR_ANALYSIS,
                    expected_version=package.version,
                )
        return reviewed
