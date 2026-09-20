from pathlib import Path

import pytest

from media_factory.domain.analysis import (
    AnalysisReviewStatus,
    ClipAnalysis,
    ClipInterval,
    DetectedEvent,
    ReviewStatus,
    SuggestedChapter,
)
from media_factory.domain.metadata import (
    MetadataChapter,
    MetadataRevisionRequest,
    MetadataStatus,
)
from media_factory.domain.models import StoredAsset
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.audit_repository import SQLAlchemyAuditRepository
from media_factory.persistence.database import Database
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.analysis_review import AnalysisReviewError, AnalysisReviewService
from media_factory.services.event_timeline import merge_clip_events
from media_factory.services.metadata_service import MetadataService


def test_review_metadata_revision_and_approval_workflow(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'metadata.sqlite3'}")
    database.create_schema()
    assets = SQLAlchemyAssetRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    analyses = SQLAlchemyAnalysisRepository(database.session_factory)
    metadata = SQLAlchemyMetadataRepository(database.session_factory)

    asset = StoredAsset(
        id="asset-metadata",
        original_name="source.mp4",
        stored_path=tmp_path / "source.mp4",
        size_bytes=100,
        sha256="d" * 64,
    )
    assets.save(asset)
    package = packages.create(asset.id)
    for target in (
        PackageState.INSPECTING,
        PackageState.READY_FOR_ANALYSIS,
        PackageState.ANALYZING,
    ):
        package = packages.transition(
            package.id,
            target=target,
            expected_version=package.version,
        )
    run = analyses.create_run(
        package_id=package.id,
        source_sha256=asset.sha256,
        provider_name="test-vlm",
        provider_version="revision-1",
        prompt_version="prompt-1",
        inference_parameters={"temperature": 0},
    )
    result = ClipAnalysis(
        summary="A person repairs a wooden item.",
        events=[
            DetectedEvent(
                relative_start=1,
                relative_end=2,
                actor="person_1",
                action="tightens_screw",
                objects=["screwdriver", "wood"],
                evidence="The person turns a screwdriver against a screw.",
                confidence=0.94,
            )
        ],
        suggested_chapters=[
            SuggestedChapter(
                relative_start=0,
                title="Repair begins",
                evidence="The tool is placed against the screw.",
                confidence=0.9,
            )
        ],
    )
    clip = analyses.add_clip(
        run_id=run.id,
        interval=ClipInterval(start=0, end=10),
        clip_path=tmp_path / "clip.mp4",
        extraction_command=["fake-ffmpeg"],
        result=result,
        inference_seconds=0.5,
    )
    analyses.save_events(merge_clip_events(run.id, [clip]))
    analyses.succeed(run.id)
    package = packages.transition(
        package.id,
        target=PackageState.AWAITING_METADATA_REVIEW,
        expected_version=package.version,
    )

    reviews = AnalysisReviewService(analyses, packages)
    with pytest.raises(AnalysisReviewError):
        reviews.review_run(run.id, approved=True)
    event = analyses.list_events(run.id)[0]
    event = reviews.review_event(
        event.id,
        review_status=ReviewStatus.APPROVED,
        expected_version=event.version,
    )
    reviewed_run = reviews.review_run(run.id, approved=True)

    assert event.version == 2
    assert reviewed_run.review_status is AnalysisReviewStatus.APPROVED

    service = MetadataService(packages=packages, analyses=analyses, metadata=metadata)
    proposal = service.propose(package.id, run.id)
    revision = service.revise(
        proposal.id,
        MetadataRevisionRequest(
            base_version=proposal.version,
            title="Repairing a Wooden Joint",
            description="A person tightens a screw in a wooden item.",
            category="Repairs_And_DIY",
            chapters=[MetadataChapter(start=0, title="Repair begins")],
            created_by="operator@example.test",
            change_note="Clarified title and description",
        ),
    )
    approved = service.approve(revision.id, expected_version=revision.version)
    history = metadata.list_for_package(package.id)

    assert proposal.category == "Repairs_And_DIY"
    assert proposal.chapters == [MetadataChapter(start=0, title="Repair begins")]
    assert [item.status for item in history] == [
        MetadataStatus.SUPERSEDED,
        MetadataStatus.APPROVED,
    ]
    assert approved.version == 2
    assert packages.get(package.id).state is PackageState.AWAITING_NARRATION_REVIEW
    audit_actions = {
        event.action
        for event in SQLAlchemyAuditRepository(database.session_factory).list_events(
            package_id=package.id
        )
    }
    assert {
        "analysis_event.reviewed",
        "analysis_run.reviewed",
        "metadata.created",
        "metadata.revised",
        "metadata.approved",
    } <= audit_actions
