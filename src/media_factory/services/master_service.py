from pathlib import Path
from typing import Protocol

from media_factory.domain.master import MasterBuild
from media_factory.domain.models import MediaInspection
from media_factory.domain.narration import AudioPolicy
from media_factory.domain.package_state import PackageState
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.master_repository import SQLAlchemyMasterRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.checksum import sha256_file


class MasterAssembler(Protocol):
    def build(
        self,
        *,
        source: Path,
        destination: Path,
        policy: AudioPolicy,
        source_duration: float,
        audio_track: Path | None,
    ) -> list[str]: ...


class DecodeValidator(Protocol):
    def validate(self, master: Path) -> list[str]: ...


class MediaInspector(Protocol):
    def inspect(self, path: Path) -> MediaInspection: ...


class MasterWorkflowError(RuntimeError):
    code = "master_workflow_blocked"


class MasterValidationError(RuntimeError):
    code = "master_validation_failed"


class MasterService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        assets: SQLAlchemyAssetRepository,
        narration: SQLAlchemyNarrationRepository,
        masters: SQLAlchemyMasterRepository,
        assembler: MasterAssembler,
        decoder: DecodeValidator,
        inspector: MediaInspector,
        master_dir: Path,
        duration_tolerance: float,
        container_extension: str,
    ) -> None:
        self.packages = packages
        self.assets = assets
        self.narration = narration
        self.masters = masters
        self.assembler = assembler
        self.decoder = decoder
        self.inspector = inspector
        self.master_dir = master_dir
        self.duration_tolerance = duration_tolerance
        self.container_extension = container_extension.lstrip(".")

    def validate_build_request(self, package_id: str) -> None:
        package = self.packages.get(package_id)
        if package.state not in {PackageState.MASTER_BUILDING, PackageState.MASTER_FAILED}:
            raise MasterWorkflowError("package is not ready for master building")
        self.narration.get_latest_audio_decision(package_id)

    def build(self, package_id: str) -> MasterBuild:
        self.validate_build_request(package_id)
        package = self.packages.get(package_id)
        if package.state is PackageState.MASTER_FAILED:
            package = self.packages.transition(
                package.id,
                target=PackageState.MASTER_BUILDING,
                expected_version=package.version,
            )
        source = self.assets.get(package.source_asset_id)
        if source.inspection is None:
            raise MasterWorkflowError("source asset has no media inspection")
        decision = self.narration.get_latest_audio_decision(package.id)
        audio_path: Path | None = None
        if decision.audio_track_id is not None:
            track = self.narration.get_audio_track(decision.audio_track_id)
            if track.package_id != package.id:
                raise MasterWorkflowError("selected audio track belongs to another package")
            audio_path = Path(track.path)

        build = self.masters.create(
            package_id=package.id,
            source_asset_id=source.id,
            source_sha256=source.sha256,
            audio_decision_id=decision.id,
        )
        destination = (
            self.master_dir
            / package.id
            / f"{build.id}.{self.container_extension}"
        )
        ffmpeg_command: list[str] = []
        try:
            ffmpeg_command = self.assembler.build(
                source=source.stored_path,
                destination=destination,
                policy=decision.policy,
                source_duration=source.inspection.duration,
                audio_track=audio_path,
            )
            inspection = self.inspector.inspect(destination)
            self._validate_inspection(source.inspection, inspection, decision.policy)
            decode_command = self.decoder.validate(destination)
            completed = self.masters.succeed(
                build.id,
                output_path=destination,
                output_size_bytes=destination.stat().st_size,
                output_sha256=sha256_file(destination),
                inspection=inspection,
                ffmpeg_command=ffmpeg_command,
                decode_command=decode_command,
            )
            self.packages.transition(
                package.id,
                target=PackageState.MASTER_READY,
                expected_version=package.version,
            )
            return completed
        except Exception as exc:
            destination.unlink(missing_ok=True)
            command = getattr(exc, "command", ffmpeg_command)
            self.masters.fail(
                build.id,
                code=str(getattr(exc, "code", "master_build_failed")),
                message=str(exc),
                ffmpeg_command=command if isinstance(command, list) else ffmpeg_command,
            )
            self.packages.transition(
                package.id,
                target=PackageState.MASTER_FAILED,
                expected_version=package.version,
            )
            raise

    def _validate_inspection(
        self,
        source: MediaInspection,
        master: MediaInspection,
        policy: AudioPolicy,
    ) -> None:
        if abs(master.duration - source.duration) > self.duration_tolerance:
            raise MasterValidationError("master duration differs from source timeline")
        source_video = source.video_streams[0]
        master_video = master.video_streams[0]
        compared_fields = ("codec_name", "width", "height", "avg_frame_rate")
        if any(
            getattr(source_video, field) != getattr(master_video, field)
            for field in compared_fields
        ):
            raise MasterValidationError("master video stream differs from source")

        source_audio_count = len(source.audio_streams)
        master_audio_count = len(master.audio_streams)
        if policy is AudioPolicy.REMOVE and master_audio_count != 0:
            raise MasterValidationError("remove policy produced an audio stream")
        if policy is AudioPolicy.PRESERVE and master_audio_count != source_audio_count:
            raise MasterValidationError("preserve policy changed the audio stream count")
        if policy is AudioPolicy.PRESERVE and [
            stream.codec_name for stream in master.audio_streams
        ] != [stream.codec_name for stream in source.audio_streams]:
            raise MasterValidationError("preserve policy changed an audio codec")
        if policy is AudioPolicy.REPLACE and master_audio_count != 1:
            raise MasterValidationError("replace policy must produce one audio stream")
        if (
            policy is AudioPolicy.ADDITIONAL
            and master_audio_count != source_audio_count + 1
        ):
            raise MasterValidationError("additional policy produced the wrong audio count")
