from pathlib import Path
from typing import Any

import pytest

from media_factory.domain.models import (
    MediaInspection,
    StoredAsset,
    StreamInfo,
    TranscriptSegment,
    TranscriptWord,
)
from media_factory.domain.narration import AudioPolicy
from media_factory.domain.package_state import PackageState
from media_factory.domain.transcription import ASRResult, TranscriptionRunState
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.master_repository import SQLAlchemyMasterRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.transcription_repository import (
    SQLAlchemyTranscriptionRepository,
)
from media_factory.services.checksum import sha256_file
from media_factory.services.transcription_service import (
    MasterChecksumMismatch,
    TranscriptionService,
    TranscriptValidationError,
)


class StubASRProvider:
    name = "stub-asr"
    version = "2.0"
    model_name = "stub-model"
    model_revision = "abc123"
    parameters: dict[str, Any] = {"word_timestamps": True}

    def __init__(self, *, include_words: bool = True) -> None:
        self.include_words = include_words

    def transcribe(self, master: Path, *, media_duration: float) -> ASRResult:
        assert master.is_file()
        words = (
            [
                TranscriptWord(word="hello", start=0.1, end=0.4),
                TranscriptWord(word="world", start=0.5, end=0.9),
            ]
            if self.include_words
            else []
        )
        return ASRResult(
            language="en",
            language_probability=0.97,
            text="hello world",
            segments=[
                TranscriptSegment(
                    start=0,
                    end=min(1, media_duration),
                    text="hello world",
                    words=words,
                )
            ],
        )


def prepare_master(tmp_path: Path) -> tuple[
    SQLAlchemyPackageRepository,
    SQLAlchemyMasterRepository,
    SQLAlchemyTranscriptionRepository,
    str,
    str,
]:
    database = Database(f"sqlite:///{tmp_path / 'transcription.sqlite3'}")
    database.create_schema()
    assets = SQLAlchemyAssetRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    narration = SQLAlchemyNarrationRepository(database.session_factory)
    masters = SQLAlchemyMasterRepository(database.session_factory)
    transcriptions = SQLAlchemyTranscriptionRepository(database.session_factory)
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    inspection = MediaInspection(
        duration=8.0,
        size_bytes=6,
        format_name="mov,mp4",
        streams=[
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ],
    )
    asset = StoredAsset(
        id="asset-transcription",
        original_name="source.mp4",
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
        PackageState.AWAITING_NARRATION_REVIEW,
    ):
        package = packages.transition(package.id, target=target, expected_version=package.version)
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
    master_path = tmp_path / "master.mp4"
    master_path.write_bytes(b"final-master")
    build = masters.create(
        package_id=package.id,
        source_asset_id=asset.id,
        source_sha256=asset.sha256,
        audio_decision_id=decision.id,
    )
    master_sha256 = sha256_file(master_path)
    masters.succeed(
        build.id,
        output_path=master_path,
        output_size_bytes=master_path.stat().st_size,
        output_sha256=master_sha256,
        inspection=inspection,
        ffmpeg_command=["ffmpeg", "-c:v", "copy"],
        decode_command=["ffmpeg", "-f", "null", "-"],
    )
    packages.transition(
        package.id,
        target=PackageState.MASTER_READY,
        expected_version=package.version,
    )
    return packages, masters, transcriptions, package.id, master_sha256


def test_transcription_is_linked_to_master_and_advances_to_packaging(
    tmp_path: Path,
) -> None:
    packages, masters, transcriptions, package_id, master_sha256 = prepare_master(tmp_path)
    service = TranscriptionService(
        packages=packages,
        masters=masters,
        transcriptions=transcriptions,
        duration_tolerance=0.5,
    )

    run = service.transcribe(package_id, StubASRProvider())

    assert run.state is TranscriptionRunState.SUCCEEDED
    assert run.master_sha256 == master_sha256
    assert run.model_revision == "abc123"
    assert run.language == "en"
    assert run.language_probability == 0.97
    assert run.transcript is not None
    assert run.transcript.master_sha256 == master_sha256
    assert len(run.transcript.segments[0].words) == 2
    assert packages.get(package_id).state is PackageState.PACKAGING


def test_missing_word_timings_blocks_pipeline(tmp_path: Path) -> None:
    packages, masters, transcriptions, package_id, _ = prepare_master(tmp_path)
    service = TranscriptionService(
        packages=packages,
        masters=masters,
        transcriptions=transcriptions,
        duration_tolerance=0.5,
    )

    with pytest.raises(TranscriptValidationError) as exc_info:
        service.transcribe(package_id, StubASRProvider(include_words=False))

    run = transcriptions.list_for_package(package_id)[0]
    assert exc_info.value.code == "no_word_timings"
    assert run.state is TranscriptionRunState.FAILED
    assert run.error_code == "no_word_timings"
    assert run.validation_issues[0].blocking is True
    assert packages.get(package_id).state is PackageState.ALIGNMENT_FAILED


def test_changed_master_is_rejected_before_asr(tmp_path: Path) -> None:
    packages, masters, transcriptions, package_id, _ = prepare_master(tmp_path)
    master = masters.get_latest_succeeded(package_id)
    assert master.output_path is not None
    Path(master.output_path).write_bytes(b"tampered-master")
    service = TranscriptionService(
        packages=packages,
        masters=masters,
        transcriptions=transcriptions,
        duration_tolerance=0.5,
    )

    with pytest.raises(MasterChecksumMismatch):
        service.transcribe(package_id, StubASRProvider())

    run = transcriptions.list_for_package(package_id)[0]
    assert run.error_code == "master_checksum_mismatch"
    assert run.transcript is None
    assert packages.get(package_id).state is PackageState.TRANSCRIPTION_FAILED
