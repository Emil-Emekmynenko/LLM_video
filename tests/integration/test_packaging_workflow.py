import json
from pathlib import Path

from media_factory.domain.analysis import AnalysisReviewStatus
from media_factory.domain.metadata import MetadataContent
from media_factory.domain.models import (
    MediaInspection,
    StoredAsset,
    StreamInfo,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from media_factory.domain.narration import AudioPolicy
from media_factory.domain.package_state import PackageState
from media_factory.domain.packaging import PackageBuildState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.master_repository import SQLAlchemyMasterRepository
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_build_repository import (
    SQLAlchemyPackageBuildRepository,
)
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.transcription_repository import (
    SQLAlchemyTranscriptionRepository,
)
from media_factory.services.checksum import sha256_file
from media_factory.services.packaging_service import PackagingService


class FakeDecoder:
    def validate(self, master: Path) -> list[str]:
        assert master.is_file()
        return ["fake-ffmpeg", "-i", str(master), "-f", "null", "-"]


def test_package_build_creates_validated_versioned_sidecars(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'packaging.sqlite3'}")
    database.create_schema()
    assets = SQLAlchemyAssetRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    analyses = SQLAlchemyAnalysisRepository(database.session_factory)
    metadata = SQLAlchemyMetadataRepository(database.session_factory)
    narration = SQLAlchemyNarrationRepository(database.session_factory)
    masters = SQLAlchemyMasterRepository(database.session_factory)
    transcriptions = SQLAlchemyTranscriptionRepository(database.session_factory)
    builds = SQLAlchemyPackageBuildRepository(database.session_factory)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    inspection = MediaInspection(
        duration=8.0,
        size_bytes=12,
        format_name="mov,mp4",
        streams=[
            StreamInfo(
                index=0,
                codec_type="video",
                codec_name="h264",
                width=1920,
                height=1080,
                avg_frame_rate="30/1",
            ),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ],
    )
    asset = StoredAsset(
        id="asset-package",
        original_name="unsafe original name.mp4",
        stored_path=source_path,
        size_bytes=6,
        sha256="a" * 64,
        inspection=inspection,
    )
    assets.save(asset)
    package = packages.create(asset.id)
    for target in (
        PackageState.INSPECTING,
        PackageState.READY_FOR_ANALYSIS,
        PackageState.ANALYZING,
        PackageState.AWAITING_METADATA_REVIEW,
    ):
        package = packages.transition(package.id, target=target, expected_version=package.version)

    analysis = analyses.create_run(
        package_id=package.id,
        source_sha256=asset.sha256,
        provider_name="fake-vlm",
        provider_version="1.0",
        prompt_version="video-analysis-v1",
        inference_parameters={},
    )
    analyses.succeed(analysis.id)
    analyses.set_review_status(analysis.id, AnalysisReviewStatus.APPROVED)
    metadata.ensure_default_categories()
    metadata_version = metadata.create(
        package_id=package.id,
        analysis_run_id=analysis.id,
        content=MetadataContent(
            title="Repairing a table",
            description="A person repairs a wooden table.",
            category="Repairs_And_DIY",
        ),
        created_by="operator@example.test",
    )
    metadata.approve(metadata_version.id, expected_version=metadata_version.version)
    package = packages.transition(
        package.id,
        target=PackageState.AWAITING_NARRATION_REVIEW,
        expected_version=package.version,
    )
    decision = narration.create_audio_decision(
        package_id=package.id,
        policy=AudioPolicy.PRESERVE,
        audio_track_id=None,
        created_by="operator@example.test",
    )
    package = packages.transition(
        package.id,
        target=PackageState.MASTER_BUILDING,
        expected_version=package.version,
    )
    master_path = tmp_path / "final-master.mp4"
    master_path.write_bytes(b"final-master")
    master_sha256 = sha256_file(master_path)
    master = masters.create(
        package_id=package.id,
        source_asset_id=asset.id,
        source_sha256=asset.sha256,
        audio_decision_id=decision.id,
    )
    masters.succeed(
        master.id,
        output_path=master_path,
        output_size_bytes=master_path.stat().st_size,
        output_sha256=master_sha256,
        inspection=inspection,
        ffmpeg_command=["ffmpeg", "-c:v", "copy"],
        decode_command=["ffmpeg", "-f", "null", "-"],
    )
    package = packages.transition(
        package.id,
        target=PackageState.MASTER_READY,
        expected_version=package.version,
    )
    package = packages.transition(
        package.id,
        target=PackageState.TRANSCRIBING,
        expected_version=package.version,
    )
    transcription = transcriptions.create(
        package_id=package.id,
        master_build_id=master.id,
        master_sha256=master_sha256,
        provider_name="fake-asr",
        provider_version="1.0",
        model_name="fixture",
        model_revision="1",
        parameters={"word_timestamps": True},
    )
    transcript = Transcript(
        asset_id=asset.id,
        master_sha256=master_sha256,
        language="en",
        media_duration=inspection.duration,
        segments=[
            TranscriptSegment(
                start=0,
                end=1,
                text="repair table",
                words=[
                    TranscriptWord(word="repair", start=0, end=0.4),
                    TranscriptWord(word="table", start=0.5, end=1),
                ],
            )
        ],
    )
    transcriptions.save_asr_result(
        transcription.id,
        transcript=transcript,
        language_probability=0.99,
        text="repair table",
    )
    transcriptions.succeed(transcription.id)
    package = packages.transition(
        package.id,
        target=PackageState.ALIGNING,
        expected_version=package.version,
    )
    packages.transition(
        package.id,
        target=PackageState.PACKAGING,
        expected_version=package.version,
    )
    service = PackagingService(
        packages=packages,
        assets=assets,
        analyses=analyses,
        metadata=metadata,
        narration=narration,
        masters=masters,
        transcriptions=transcriptions,
        builds=builds,
        decoder=FakeDecoder(),
        package_dir=tmp_path / "packages",
        customer="internal",
        schema_version="1.0",
        duration_tolerance=0.5,
    )

    build = service.build(package.id)

    assert build.state is PackageBuildState.SUCCEEDED
    assert build.version == 1
    assert build.computed_metadata is not None
    assert build.computed_metadata.wpm == 15.0
    assert build.computed_metadata.resolution == "1920x1080"
    assert build.validation_issues == []
    assert build.manifest_path is not None
    output_dir = Path(build.output_dir or "")
    assert output_dir.name == "v0001"
    assert {path.name for path in output_dir.iterdir()} == {
        f"{build.base_name}.mp4",
        f"{build.base_name}_transcript.json",
        f"{build.base_name}_metadata.json",
        f"{build.base_name}_manifest.json",
    }
    metadata_payload = json.loads(
        (output_dir / f"{build.base_name}_metadata.json").read_text()
    )
    assert list(metadata_payload) == [
        "Title",
        "Description",
        "Publication Date",
        "Category",
        "Duration",
        "Language",
        "Resolution",
        "WPM",
    ]
    manifest = json.loads(Path(build.manifest_path).read_text())
    assert build.base_name == "Repairs_And_DIY_Repairing_a_table"
    assert manifest["customer_schema_sha256"] == build.build_parameters[
        "customer_schema_sha256"
    ]
    assert manifest["master_sha256"] == master_sha256
    assert manifest["transcript_master_sha256"] == master_sha256
    assert manifest["validation"] == []
    assert packages.get(package.id).state is PackageState.AWAITING_QA
