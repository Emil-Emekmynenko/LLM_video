from pathlib import Path

from media_factory.domain.master import MasterBuild, MasterBuildState
from media_factory.domain.models import Transcript
from media_factory.domain.package_state import PackageState
from media_factory.domain.transcription import TranscriptionRun
from media_factory.persistence.master_repository import SQLAlchemyMasterRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.transcription_repository import (
    SQLAlchemyTranscriptionRepository,
)
from media_factory.providers.speech_recognition import SpeechRecognitionProvider
from media_factory.services.checksum import sha256_file
from media_factory.services.transcript_validator import validate_transcript


class TranscriptionWorkflowError(RuntimeError):
    code = "transcription_workflow_blocked"


class MasterChecksumMismatch(RuntimeError):
    code = "master_checksum_mismatch"


class TranscriptValidationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TranscriptionService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        masters: SQLAlchemyMasterRepository,
        transcriptions: SQLAlchemyTranscriptionRepository,
        duration_tolerance: float,
    ) -> None:
        self.packages = packages
        self.masters = masters
        self.transcriptions = transcriptions
        self.duration_tolerance = duration_tolerance

    def validate_request(self, package_id: str) -> MasterBuild:
        package = self.packages.get(package_id)
        if package.state not in {
            PackageState.MASTER_READY,
            PackageState.TRANSCRIPTION_FAILED,
            PackageState.ALIGNMENT_FAILED,
        }:
            raise TranscriptionWorkflowError("package is not ready for transcription")
        master = self.masters.get_latest_succeeded(package_id)
        if (
            master.state is not MasterBuildState.SUCCEEDED
            or master.output_path is None
            or master.output_sha256 is None
            or master.inspection is None
        ):
            raise TranscriptionWorkflowError("package has no complete final master")
        return master

    def transcribe(
        self,
        package_id: str,
        provider: SpeechRecognitionProvider,
    ) -> TranscriptionRun:
        master = self.validate_request(package_id)
        assert master.output_path is not None
        assert master.output_sha256 is not None
        assert master.inspection is not None
        package = self.packages.get(package_id)
        package = self.packages.transition(
            package.id,
            target=PackageState.TRANSCRIBING,
            expected_version=package.version,
        )
        run = self.transcriptions.create(
            package_id=package.id,
            master_build_id=master.id,
            master_sha256=master.output_sha256,
            provider_name=provider.name,
            provider_version=provider.version,
            model_name=provider.model_name,
            model_revision=provider.model_revision,
            parameters=provider.parameters,
        )
        master_path = Path(master.output_path)
        try:
            if sha256_file(master_path) != master.output_sha256:
                raise MasterChecksumMismatch("final master no longer matches its recorded SHA-256")
            result = provider.transcribe(
                master_path,
                media_duration=master.inspection.duration,
            )
            transcript = Transcript(
                asset_id=master.source_asset_id,
                master_sha256=master.output_sha256,
                language=result.language,
                media_duration=master.inspection.duration,
                segments=result.segments,
            )
            run = self.transcriptions.save_asr_result(
                run.id,
                transcript=transcript,
                language_probability=result.language_probability,
                text=result.text,
            )
            package = self.packages.transition(
                package.id,
                target=PackageState.ALIGNING,
                expected_version=package.version,
            )
        except Exception as exc:
            self.transcriptions.fail(
                run.id,
                code=str(getattr(exc, "code", "transcription_failed")),
                message=str(exc),
            )
            self.packages.transition(
                package.id,
                target=PackageState.TRANSCRIPTION_FAILED,
                expected_version=package.version,
            )
            raise

        issues = validate_transcript(
            transcript,
            duration_tolerance=self.duration_tolerance,
        )
        blocking = [issue for issue in issues if issue.blocking]
        if blocking:
            first = blocking[0]
            self.transcriptions.fail(
                run.id,
                code=first.code,
                message=first.message,
                issues=issues,
            )
            self.packages.transition(
                package.id,
                target=PackageState.ALIGNMENT_FAILED,
                expected_version=package.version,
            )
            raise TranscriptValidationError(first.code, first.message)

        completed = self.transcriptions.succeed(run.id)
        self.packages.transition(
            package.id,
            target=PackageState.PACKAGING,
            expected_version=package.version,
        )
        return completed
