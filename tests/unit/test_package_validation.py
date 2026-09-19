from pathlib import Path
from typing import Any, cast

from media_factory.domain.analysis import AnalysisReviewStatus
from media_factory.domain.metadata import MetadataStatus
from media_factory.domain.models import Transcript, TranscriptSegment, TranscriptWord
from media_factory.domain.packaging import PackageFile
from media_factory.services.checksum import sha256_file
from media_factory.services.packaging_service import PackagingService


class FakeDecoder:
    def validate(self, master: Path) -> list[str]:
        return ["decode", str(master)]


def test_stale_transcript_is_a_blocking_package_error(tmp_path: Path) -> None:
    master = tmp_path / "asset-package.mp4"
    master.write_bytes(b"master")
    digest = sha256_file(master)
    transcript = Transcript(
        asset_id="asset-1",
        master_sha256="b" * 64,
        media_duration=2,
        segments=[
            TranscriptSegment(
                start=0,
                end=1,
                text="hello",
                words=[TranscriptWord(word="hello", start=0, end=1)],
            )
        ],
    )
    file = PackageFile(
        role="master",
        filename=master.name,
        size_bytes=master.stat().st_size,
        sha256=digest,
        mime_type="video/mp4",
        local_relative_path=master.name,
        target_relative_path=master.name,
    )
    service = PackagingService(
        packages=cast(Any, None),
        assets=cast(Any, None),
        analyses=cast(Any, None),
        metadata=cast(Any, None),
        narration=cast(Any, None),
        masters=cast(Any, None),
        transcriptions=cast(Any, None),
        builds=cast(Any, None),
        decoder=FakeDecoder(),
        package_dir=tmp_path,
        customer="internal",
        schema_version="1.0",
        duration_tolerance=0.5,
    )

    issues = service._validate(
        master_path=master,
        artifact_dir=tmp_path,
        master_sha256=digest,
        master_build_id="master-current",
        master_audio_decision_id="audio-1",
        transcript_master_build_id="master-old",
        transcript_master_sha256="b" * 64,
        transcript=transcript,
        metadata_status=MetadataStatus.APPROVED,
        analysis_review_status=AnalysisReviewStatus.APPROVED,
        latest_audio_decision_id="audio-1",
        source_duplicate_of=None,
        resolution="1920x1080",
        files=[file],
    )

    stale = [issue for issue in issues if issue.code == "stale_transcript"]
    assert len(stale) == 1
    assert stale[0].blocking is True
