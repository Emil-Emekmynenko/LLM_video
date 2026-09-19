from pathlib import Path

from media_factory.domain.models import MediaInspection, StoredAsset, StreamInfo
from media_factory.domain.narration import AudioPolicy
from media_factory.domain.package_state import PackageState
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.master_repository import SQLAlchemyMasterRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.master_service import MasterService


class FakeAssembler:
    def build(
        self,
        *,
        source: Path,
        destination: Path,
        policy: AudioPolicy,
        source_duration: float,
        audio_track: Path | None,
    ) -> list[str]:
        del source, policy, source_duration, audio_track
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"verified-master")
        return ["fake-ffmpeg", "-c:v", "copy", str(destination)]


class FakeDecoder:
    def validate(self, master: Path) -> list[str]:
        assert master.is_file()
        return ["fake-ffmpeg", "-i", str(master), "-f", "null", "-"]


class FakeInspector:
    def __init__(self, inspection: MediaInspection) -> None:
        self.inspection = inspection

    def inspect(self, path: Path) -> MediaInspection:
        assert path.is_file()
        return self.inspection


def test_master_is_ready_only_after_inspection_decode_and_checksum(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'master.sqlite3'}")
    database.create_schema()
    assets = SQLAlchemyAssetRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    narration = SQLAlchemyNarrationRepository(database.session_factory)
    masters = SQLAlchemyMasterRepository(database.session_factory)
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    inspection = MediaInspection(
        duration=12.5,
        size_bytes=6,
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
        id="asset-master",
        original_name="source.mp4",
        stored_path=source_path,
        size_bytes=6,
        sha256="f" * 64,
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
    service = MasterService(
        packages=packages,
        assets=assets,
        narration=narration,
        masters=masters,
        assembler=FakeAssembler(),
        decoder=FakeDecoder(),
        inspector=FakeInspector(inspection),
        master_dir=tmp_path / "masters",
        duration_tolerance=0.1,
        container_extension="mp4",
    )

    build = service.build(package.id)

    assert build.audio_decision_id == decision.id
    assert build.output_sha256 is not None
    assert build.video_stream_copy is True
    assert build.inspection == inspection
    assert packages.get(package.id).state is PackageState.MASTER_READY
