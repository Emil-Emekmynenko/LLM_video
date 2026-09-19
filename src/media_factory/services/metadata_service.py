from media_factory.domain.analysis import (
    AnalysisClip,
    AnalysisReviewStatus,
    AnalysisRunState,
    ReviewStatus,
    TimelineEvent,
)
from media_factory.domain.metadata import (
    MetadataChapter,
    MetadataContent,
    MetadataRevisionRequest,
    MetadataVersion,
)
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository


class MetadataWorkflowError(RuntimeError):
    code = "metadata_workflow_blocked"


class MetadataService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        analyses: SQLAlchemyAnalysisRepository,
        metadata: SQLAlchemyMetadataRepository,
    ) -> None:
        self.packages = packages
        self.analyses = analyses
        self.metadata = metadata

    def propose(self, package_id: str, analysis_run_id: str) -> MetadataVersion:
        package = self.packages.get(package_id)
        run = self.analyses.get(analysis_run_id)
        if package.state is not PackageState.AWAITING_METADATA_REVIEW:
            raise MetadataWorkflowError("package is not awaiting metadata review")
        if run.package_id != package.id:
            raise MetadataWorkflowError("analysis run belongs to another package")
        if run.state is not AnalysisRunState.SUCCEEDED:
            raise MetadataWorkflowError("analysis run has not succeeded")
        if run.review_status is not AnalysisReviewStatus.APPROVED:
            raise MetadataWorkflowError("analysis run must be approved first")

        content = self._build_content(analysis_run_id)
        self.metadata.ensure_default_categories()
        return self.metadata.create(
            package_id=package.id,
            analysis_run_id=run.id,
            content=content,
            created_by="system",
            change_note="Initial proposal from approved analysis",
        )

    def revise(
        self,
        metadata_id: str,
        request: MetadataRevisionRequest,
    ) -> MetadataVersion:
        current = self.metadata.get(metadata_id)
        package = self.packages.get(current.package_id)
        if package.state is not PackageState.AWAITING_METADATA_REVIEW:
            raise MetadataWorkflowError("metadata can only be edited during review")
        content = MetadataContent(
            title=request.title,
            description=request.description,
            category=request.category,
            chapters=request.chapters,
            narration_language=request.narration_language,
        )
        return self.metadata.revise(
            metadata_id,
            base_version=request.base_version,
            content=content,
            created_by=request.created_by,
            change_note=request.change_note,
        )

    def approve(self, metadata_id: str, *, expected_version: int) -> MetadataVersion:
        current = self.metadata.get(metadata_id)
        package = self.packages.get(current.package_id)
        if package.state is not PackageState.AWAITING_METADATA_REVIEW:
            raise MetadataWorkflowError("package is not awaiting metadata review")
        approved = self.metadata.approve(metadata_id, expected_version=expected_version)
        self.packages.transition(
            package.id,
            target=PackageState.AWAITING_NARRATION_REVIEW,
            expected_version=package.version,
        )
        return approved

    def _build_content(self, run_id: str) -> MetadataContent:
        events = [
            event
            for event in self.analyses.list_events(run_id)
            if event.review_status is ReviewStatus.APPROVED
        ]
        clips = self.analyses.list_clips(run_id)
        if events:
            first = events[0]
            readable_action = first.action.replace("_", " ").strip().title()
            subject = first.objects[0].replace("_", " ") if first.objects else ""
            title = f"{readable_action} {subject}".strip()
            evidence = [item for event in events[:3] for item in event.evidence]
            description = " ".join(dict.fromkeys(evidence))
            category = self._category_for(events)
        elif clips:
            title = "Video Activity"
            description = " ".join(
                dict.fromkeys(clip.result.summary for clip in clips[:3])
            )
            category = "Other"
        else:
            raise MetadataWorkflowError("approved analysis contains no observations")

        chapters = self._chapters(clips, events)
        return MetadataContent(
            title=title[:200],
            description=description[:5000],
            category=category,
            chapters=chapters,
        )

    @staticmethod
    def _chapters(
        clips: list[AnalysisClip],
        events: list[TimelineEvent],
    ) -> list[MetadataChapter]:
        proposed: dict[float, str] = {}
        for clip in clips:
            for chapter in clip.result.suggested_chapters:
                start = round(clip.interval.start + chapter.relative_start, 3)
                proposed.setdefault(start, chapter.title)
        if not proposed and events:
            first = events[0]
            proposed[round(first.start, 3)] = first.action.replace("_", " ").title()
        return [
            MetadataChapter(start=start, title=title)
            for start, title in sorted(proposed.items())
        ]

    @staticmethod
    def _category_for(events: list[TimelineEvent]) -> str:
        text = " ".join(
            [
                event.action + " " + " ".join(event.objects)
                for event in events
            ]
        ).casefold()
        mappings = (
            ("Repairs_And_DIY", ("repair", "screwdriver", "hammer", "drill", "wood")),
            ("Cooking", ("cook", "knife", "food", "pan", "bake")),
            ("Arts_And_Crafts", ("paint", "draw", "craft", "clay")),
            ("Technology", ("computer", "phone", "keyboard", "code")),
            ("Nature", ("plant", "animal", "forest", "garden")),
            ("Sports_And_Fitness", ("run", "exercise", "ball", "workout")),
        )
        for category, keywords in mappings:
            if any(keyword in text for keyword in keywords):
                return category
        return "Other"
