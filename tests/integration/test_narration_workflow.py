from pathlib import Path

import pytest

from media_factory.domain.metadata import MetadataContent
from media_factory.domain.models import StoredAsset
from media_factory.domain.narration import (
    AudioDecisionRequest,
    AudioPolicy,
    NarrationProposalRequest,
    NarrationRevisionRequest,
    NarrationScriptStatus,
)
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.providers.text_to_speech import FakeTextToSpeechProvider
from media_factory.services.narration_service import NarrationService, NarrationWorkflowError


def test_approved_script_tts_and_audio_decision_workflow(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'narration.sqlite3'}")
    database.create_schema()
    assets = SQLAlchemyAssetRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    analyses = SQLAlchemyAnalysisRepository(database.session_factory)
    metadata = SQLAlchemyMetadataRepository(database.session_factory)
    narration = SQLAlchemyNarrationRepository(database.session_factory)

    asset = StoredAsset(
        id="asset-narration",
        original_name="source.mp4",
        stored_path=tmp_path / "source.mp4",
        size_bytes=100,
        sha256="e" * 64,
    )
    assets.save(asset)
    package = packages.create(asset.id)
    for target in (
        PackageState.INSPECTING,
        PackageState.READY_FOR_ANALYSIS,
        PackageState.ANALYZING,
    ):
        package = packages.transition(package.id, target=target, expected_version=package.version)
    run = analyses.create_run(
        package_id=package.id,
        source_sha256=asset.sha256,
        provider_name="test-vlm",
        provider_version="revision-1",
        prompt_version="prompt-1",
        inference_parameters={},
    )
    analyses.succeed(run.id)
    package = packages.transition(
        package.id,
        target=PackageState.AWAITING_METADATA_REVIEW,
        expected_version=package.version,
    )
    metadata.ensure_default_categories()
    metadata_version = metadata.create(
        package_id=package.id,
        analysis_run_id=run.id,
        content=MetadataContent(
            title="Repairing a Joint",
            description="A person tightens a screw in a wooden joint.",
            category="Repairs_And_DIY",
            narration_language="en",
        ),
        created_by="operator@example.test",
    )
    metadata_version = metadata.approve(
        metadata_version.id,
        expected_version=metadata_version.version,
    )
    package = packages.transition(
        package.id,
        target=PackageState.AWAITING_NARRATION_REVIEW,
        expected_version=package.version,
    )
    service = NarrationService(
        packages=packages,
        metadata=metadata,
        narration=narration,
        narration_dir=tmp_path / "narration",
        allow_additional_audio=False,
    )

    proposal = service.propose_script(
        package.id,
        NarrationProposalRequest(
            metadata_version_id=metadata_version.id,
            style="concise and factual",
            target_wpm=120,
        ),
    )
    with pytest.raises(NarrationWorkflowError, match="approved script"):
        service.validate_tts_request(package.id, proposal.id)

    revision = service.revise_script(
        proposal.id,
        NarrationRevisionRequest(
            base_version=proposal.version,
            text="The person tightens a screw in the wooden joint.",
            language="en",
            style="concise and factual",
            target_wpm=120,
            created_by="operator@example.test",
            change_note="Adjusted narration wording",
        ),
    )
    approved = service.approve_script(revision.id, expected_version=revision.version)
    track = service.generate_audio(
        package.id,
        approved.id,
        FakeTextToSpeechProvider(),
    )

    assert approved.status is NarrationScriptStatus.APPROVED
    assert track.synthetic is True
    assert Path(track.path).is_file()
    assert packages.get(package.id).state is PackageState.AWAITING_NARRATION_REVIEW
    with pytest.raises(NarrationWorkflowError, match="disabled"):
        service.decide_audio(
            package.id,
            AudioDecisionRequest(
                policy=AudioPolicy.ADDITIONAL,
                audio_track_id=track.id,
                created_by="operator@example.test",
            ),
        )

    decision = service.decide_audio(
        package.id,
        AudioDecisionRequest(
            policy=AudioPolicy.REPLACE,
            audio_track_id=track.id,
            created_by="operator@example.test",
        ),
    )

    assert decision.policy is AudioPolicy.REPLACE
    assert packages.get(package.id).state is PackageState.MASTER_BUILDING
    assert [script.status for script in narration.list_scripts(package.id)] == [
        NarrationScriptStatus.SUPERSEDED,
        NarrationScriptStatus.APPROVED,
    ]
