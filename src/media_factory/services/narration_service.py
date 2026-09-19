from pathlib import Path

from media_factory.domain.metadata import MetadataStatus
from media_factory.domain.narration import (
    AudioDecision,
    AudioDecisionRequest,
    AudioPolicy,
    AudioTrack,
    NarrationProposalRequest,
    NarrationRevisionRequest,
    NarrationScript,
    NarrationScriptContent,
    NarrationScriptStatus,
)
from media_factory.domain.package_state import PackageState
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.providers.text_to_speech import TextToSpeechProvider
from media_factory.services.checksum import sha256_file


class NarrationWorkflowError(RuntimeError):
    code = "narration_workflow_blocked"


class NarrationService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        metadata: SQLAlchemyMetadataRepository,
        narration: SQLAlchemyNarrationRepository,
        narration_dir: Path,
        allow_additional_audio: bool,
    ) -> None:
        self.packages = packages
        self.metadata = metadata
        self.narration = narration
        self.narration_dir = narration_dir
        self.allow_additional_audio = allow_additional_audio

    def propose_script(
        self,
        package_id: str,
        request: NarrationProposalRequest,
    ) -> NarrationScript:
        package = self.packages.get(package_id)
        metadata = self.metadata.get(request.metadata_version_id)
        if package.state is not PackageState.AWAITING_NARRATION_REVIEW:
            raise NarrationWorkflowError("package is not awaiting narration review")
        if metadata.package_id != package.id or metadata.status is not MetadataStatus.APPROVED:
            raise NarrationWorkflowError("an approved metadata version is required")
        if metadata.narration_language is None:
            raise NarrationWorkflowError("metadata does not request narration")
        content = NarrationScriptContent(
            text=metadata.description,
            language=metadata.narration_language,
            style=request.style,
            target_wpm=request.target_wpm,
        )
        return self.narration.create_script(
            package_id=package.id,
            metadata_version_id=metadata.id,
            content=content,
            created_by="system",
            change_note="Initial narration proposal from approved metadata",
        )

    def revise_script(
        self,
        script_id: str,
        request: NarrationRevisionRequest,
    ) -> NarrationScript:
        script = self.narration.get_script(script_id)
        package = self.packages.get(script.package_id)
        if package.state is not PackageState.AWAITING_NARRATION_REVIEW:
            raise NarrationWorkflowError("script can only be edited during narration review")
        content = NarrationScriptContent(
            text=request.text,
            language=request.language,
            style=request.style,
            target_wpm=request.target_wpm,
        )
        return self.narration.revise_script(
            script_id,
            base_version=request.base_version,
            content=content,
            created_by=request.created_by,
            change_note=request.change_note,
        )

    def approve_script(self, script_id: str, *, expected_version: int) -> NarrationScript:
        script = self.narration.get_script(script_id)
        package = self.packages.get(script.package_id)
        if package.state is not PackageState.AWAITING_NARRATION_REVIEW:
            raise NarrationWorkflowError("package is not awaiting narration review")
        return self.narration.approve_script(script_id, expected_version=expected_version)

    def validate_tts_request(self, package_id: str, script_id: str) -> NarrationScript:
        package = self.packages.get(package_id)
        script = self.narration.get_script(script_id)
        if package.state is not PackageState.AWAITING_NARRATION_REVIEW:
            raise NarrationWorkflowError("package is not awaiting narration review")
        if script.package_id != package.id:
            raise NarrationWorkflowError("script belongs to another package")
        if script.status is not NarrationScriptStatus.APPROVED:
            raise NarrationWorkflowError("TTS requires an approved script")
        return script

    def generate_audio(
        self,
        package_id: str,
        script_id: str,
        provider: TextToSpeechProvider,
    ) -> AudioTrack:
        script = self.validate_tts_request(package_id, script_id)
        package = self.packages.get(package_id)
        package = self.packages.transition(
            package.id,
            target=PackageState.GENERATING_NARRATION,
            expected_version=package.version,
        )
        run = self.narration.create_tts_run(
            package_id=package.id,
            script_id=script.id,
            provider_name=provider.name,
            provider_version=provider.version,
            parameters=provider.parameters,
        )
        try:
            destination = self.narration_dir / package.id / f"{run.id}.wav"
            artifact = provider.synthesize(script, destination)
            track = self.narration.add_audio_track(
                package_id=package.id,
                tts_run_id=run.id,
                script_id=script.id,
                path=artifact.path,
                mime_type=artifact.mime_type,
                size_bytes=artifact.path.stat().st_size,
                sha256=sha256_file(artifact.path),
                duration=artifact.duration,
                language=script.language,
                synthetic=artifact.synthetic,
            )
            self.narration.succeed_tts_run(run.id)
            self.packages.transition(
                package.id,
                target=PackageState.AWAITING_NARRATION_REVIEW,
                expected_version=package.version,
            )
            return track
        except Exception as exc:
            self.narration.fail_tts_run(
                run.id,
                code=getattr(exc, "code", "tts_failed"),
                message=str(exc),
            )
            self.packages.transition(
                package.id,
                target=PackageState.AWAITING_NARRATION_REVIEW,
                expected_version=package.version,
            )
            raise

    def decide_audio(
        self,
        package_id: str,
        request: AudioDecisionRequest,
    ) -> AudioDecision:
        package = self.packages.get(package_id)
        if package.state is not PackageState.AWAITING_NARRATION_REVIEW:
            raise NarrationWorkflowError("package is not awaiting narration review")
        if request.policy is AudioPolicy.ADDITIONAL and not self.allow_additional_audio:
            raise NarrationWorkflowError("additional audio tracks are disabled")
        if request.audio_track_id is not None:
            track = self.narration.get_audio_track(request.audio_track_id)
            if track.package_id != package.id:
                raise NarrationWorkflowError("audio track belongs to another package")
            script = self.narration.get_script(track.script_id)
            if script.status is not NarrationScriptStatus.APPROVED:
                raise NarrationWorkflowError("audio track must come from an approved script")
        decision = self.narration.create_audio_decision(
            package_id=package.id,
            policy=request.policy,
            audio_track_id=request.audio_track_id,
            created_by=request.created_by,
        )
        self.packages.transition(
            package.id,
            target=PackageState.MASTER_BUILDING,
            expected_version=package.version,
        )
        return decision
