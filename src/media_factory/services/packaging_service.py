import shutil
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from media_factory.domain.analysis import AnalysisReviewStatus
from media_factory.domain.customer_schema import CustomerSchema
from media_factory.domain.metadata import MetadataStatus
from media_factory.domain.models import Severity, Transcript, ValidationIssue
from media_factory.domain.package_state import PackageState
from media_factory.domain.packaging import (
    DeliveryMetadata,
    PackageBuild,
    PackageFile,
)
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
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
from media_factory.services.customer_schema_service import load_customer_schema
from media_factory.services.package_artifacts import (
    calculate_wpm,
    deterministic_json_bytes,
    ensure_safe_relative_path,
    ordered_metadata,
    semantic_package_base_name,
    write_deterministic_json,
)
from media_factory.services.transcript_validator import validate_transcript


class DecodeValidator(Protocol):
    def validate(self, master: Path) -> list[str]: ...


class PackagingWorkflowError(RuntimeError):
    code = "packaging_workflow_blocked"


class PackageValidationFailed(RuntimeError):
    code = "package_validation_failed"

    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__(f"package has {len(issues)} blocking validation issue(s)")
        self.issues = issues


class PackagingService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        assets: SQLAlchemyAssetRepository,
        analyses: SQLAlchemyAnalysisRepository,
        metadata: SQLAlchemyMetadataRepository,
        narration: SQLAlchemyNarrationRepository,
        masters: SQLAlchemyMasterRepository,
        transcriptions: SQLAlchemyTranscriptionRepository,
        builds: SQLAlchemyPackageBuildRepository,
        decoder: DecodeValidator,
        package_dir: Path,
        customer: str,
        schema_version: str,
        duration_tolerance: float,
        schema_dir: Path | None = None,
    ) -> None:
        self.packages = packages
        self.assets = assets
        self.analyses = analyses
        self.metadata = metadata
        self.narration = narration
        self.masters = masters
        self.transcriptions = transcriptions
        self.builds = builds
        self.decoder = decoder
        self.package_dir = package_dir
        self.schema: CustomerSchema = load_customer_schema(
            customer, schema_version, schema_dir=schema_dir
        )
        self.customer = self.schema.customer
        self.schema_version = self.schema.version
        self.duration_tolerance = self.schema.transcript_duration_tolerance
        self.configured_duration_tolerance = duration_tolerance
        self.schema_sha256 = sha256(
            deterministic_json_bytes(self.schema.model_dump(mode="json"))
        ).hexdigest()

    def validate_request(self, package_id: str) -> None:
        package = self.packages.get(package_id)
        if package.state not in {PackageState.PACKAGING, PackageState.VALIDATION_FAILED}:
            raise PackagingWorkflowError("package is not ready for packaging")
        self.masters.get_latest_succeeded(package_id)
        self.transcriptions.get_latest_succeeded(package_id)
        self.metadata.get_latest_approved(package_id)

    def build(self, package_id: str) -> PackageBuild:
        self.validate_request(package_id)
        package = self.packages.get(package_id)
        if package.state is PackageState.VALIDATION_FAILED:
            package = self.packages.transition(
                package.id,
                target=PackageState.PACKAGING,
                expected_version=package.version,
            )
        master = self.masters.get_latest_succeeded(package.id)
        transcription = self.transcriptions.get_latest_succeeded(package.id)
        approved_metadata = self.metadata.get_latest_approved(package.id)
        analysis = self.analyses.get(approved_metadata.analysis_run_id)
        source = self.assets.get(package.source_asset_id)
        audio_decision = self.narration.get_latest_audio_decision(package.id)
        base_name = semantic_package_base_name(
            approved_metadata.category,
            approved_metadata.title,
            package.id,
        )
        build = self.builds.create(
            package_id=package.id,
            master_build_id=master.id,
            transcription_run_id=transcription.id,
            metadata_version_id=approved_metadata.id,
            delivery_id=f"local-{package.id}",
            customer=self.customer,
            schema_version=self.schema_version,
            base_name=base_name,
            build_parameters={
                "wpm_algorithm": "full_duration_v1",
                "duration_tolerance": self.duration_tolerance,
                "customer_schema_sha256": self.schema_sha256,
            },
        )
        output_dir = self.package_dir / package.id / f"v{build.version:04d}"
        staging_dir = self.package_dir / package.id / f".{build.id}.tmp"
        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            if (
                master.output_path is None
                or master.output_sha256 is None
                or master.inspection is None
                or transcription.transcript is None
            ):
                raise PackagingWorkflowError("packaging inputs are incomplete")
            transcript = transcription.transcript
            video = master.inspection.video_streams[0] if master.inspection.video_streams else None
            resolution = f"{video.width}x{video.height}" if video else "unknown"
            metadata_payload = ordered_metadata(
                {
                    "Title": approved_metadata.title,
                    "Description": approved_metadata.description,
                    "Publication Date": None,
                    "Category": approved_metadata.category,
                    "Duration": master.inspection.duration,
                    "Language": transcript.language,
                    "Resolution": resolution,
                    "WPM": calculate_wpm(transcript),
                },
                list(self.schema.metadata_key_order),
            )
            delivery_metadata = DeliveryMetadata.model_validate(metadata_payload)
            transcript_name = f"{base_name}_transcript.json"
            metadata_name = f"{base_name}_metadata.json"
            master_name = f"{base_name}{Path(master.output_path).suffix.lower()}"
            packaged_master_path = staging_dir / master_name
            transcript_path = staging_dir / transcript_name
            metadata_path = staging_dir / metadata_name
            _materialize_master(Path(master.output_path), packaged_master_path)
            write_deterministic_json(
                transcript_path,
                transcript.model_dump(mode="json"),
            )
            write_deterministic_json(
                metadata_path,
                metadata_payload,
            )
            files = self._file_entries(
                master_path=packaged_master_path,
                master_sha256=master.output_sha256,
                base_name=base_name,
                transcript_path=transcript_path,
                metadata_path=metadata_path,
            )
            issues = self._validate(
                master_path=packaged_master_path,
                artifact_dir=staging_dir,
                master_sha256=master.output_sha256,
                master_build_id=master.id,
                master_audio_decision_id=master.audio_decision_id,
                transcript_master_build_id=transcription.master_build_id,
                transcript_master_sha256=transcription.master_sha256,
                transcript=transcript,
                metadata_status=approved_metadata.status,
                metadata_category=approved_metadata.category,
                has_chapters=bool(approved_metadata.chapters),
                analysis_review_status=analysis.review_status,
                latest_audio_decision_id=audio_decision.id,
                audio_policy=audio_decision.policy.value,
                synthetic_audio=(
                    self.narration.get_audio_track(audio_decision.audio_track_id).synthetic
                    if audio_decision.audio_track_id is not None
                    else False
                ),
                source_duplicate_of=source.duplicate_of,
                resolution=resolution,
                base_name=base_name,
                files=files,
            )
            blocking = [issue for issue in issues if issue.blocking]
            manifest_name = f"{base_name}_manifest.json"
            manifest_path = staging_dir / manifest_name
            manifest = {
                "schema_version": "1.0",
                "package_id": package.id,
                "delivery_id": build.delivery_id,
                "build_version": build.version,
                "customer": self.customer,
                "customer_schema_version": self.schema_version,
                "customer_schema_sha256": self.schema_sha256,
                "base_name": base_name,
                "master_build_id": master.id,
                "master_sha256": master.output_sha256,
                "transcript_master_sha256": transcript.master_sha256,
                "input_versions": {
                    "metadata": approved_metadata.version,
                    "transcription_run_id": transcription.id,
                },
                "models": {
                    "asr": {
                        "provider": transcription.provider_name,
                        "provider_version": transcription.provider_version,
                        "model": transcription.model_name,
                        "model_revision": transcription.model_revision,
                    },
                    "vlm": {
                        "provider": analysis.provider_name,
                        "provider_version": analysis.provider_version,
                        "prompt_version": analysis.prompt_version,
                    },
                },
                "wpm_calculation": {
                    "algorithm": "full_duration_v1",
                    "value": delivery_metadata.wpm,
                },
                "files": [item.model_dump(mode="json") for item in files],
                "validation": [issue.model_dump(mode="json") for issue in issues],
                "built_at": build.created_at.isoformat(),
                "approvers": {
                    "metadata": approved_metadata.created_by,
                    "qa": None,
                },
            }
            write_deterministic_json(manifest_path, manifest)
            manifest_sha256 = sha256_file(manifest_path)
            staging_dir.replace(output_dir)
            final_manifest_path = output_dir / manifest_name
            final_files = [
                item.model_copy(
                    update={
                        "local_relative_path": item.filename,
                        "target_relative_path": item.filename,
                    }
                )
                for item in files
            ]
            if blocking:
                completed = self.builds.fail(
                    build.id,
                    code=blocking[0].code,
                    message=blocking[0].message,
                    issues=issues,
                    output_dir=str(output_dir),
                    files=final_files,
                    manifest_path=str(final_manifest_path),
                    manifest_sha256=manifest_sha256,
                    computed_metadata=delivery_metadata,
                )
                self.packages.transition(
                    package.id,
                    target=PackageState.VALIDATION_FAILED,
                    expected_version=package.version,
                )
                raise PackageValidationFailed(blocking)
            completed = self.builds.succeed(
                build.id,
                output_dir=str(output_dir),
                files=final_files,
                manifest_path=str(final_manifest_path),
                manifest_sha256=manifest_sha256,
                computed_metadata=delivery_metadata,
            )
            self.packages.transition(
                package.id,
                target=PackageState.AWAITING_QA,
                expected_version=package.version,
            )
            return completed
        except PackageValidationFailed:
            raise
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.builds.fail(
                build.id,
                code=str(getattr(exc, "code", "package_build_failed")),
                message=str(exc),
                issues=[],
            )
            self.packages.transition(
                package.id,
                target=PackageState.VALIDATION_FAILED,
                expected_version=package.version,
            )
            raise

    def _file_entries(
        self,
        *,
        master_path: Path,
        master_sha256: str,
        base_name: str,
        transcript_path: Path,
        metadata_path: Path,
    ) -> list[PackageFile]:
        master_name = f"{base_name}{master_path.suffix.lower()}"
        return [
            PackageFile(
                role="master",
                filename=master_name,
                size_bytes=master_path.stat().st_size,
                sha256=master_sha256,
                mime_type=_master_mime_type(master_path),
                local_relative_path=master_name,
                target_relative_path=master_name,
            ),
            _json_file("transcript", transcript_path),
            _json_file("metadata", metadata_path),
        ]

    def _validate(
        self,
        *,
        master_path: Path,
        artifact_dir: Path,
        master_sha256: str,
        master_build_id: str,
        master_audio_decision_id: str,
        transcript_master_build_id: str,
        transcript_master_sha256: str,
        transcript: Transcript,
        metadata_status: MetadataStatus,
        metadata_category: str | None = None,
        has_chapters: bool = True,
        analysis_review_status: AnalysisReviewStatus,
        latest_audio_decision_id: str,
        audio_policy: str = "preserve",
        synthetic_audio: bool = False,
        source_duplicate_of: str | None,
        resolution: str,
        base_name: str | None = None,
        files: list[PackageFile],
    ) -> list[ValidationIssue]:
        issues = validate_transcript(
            transcript,
            duration_tolerance=self.duration_tolerance,
        )
        if (
            transcript.master_sha256 != master_sha256
            or transcript_master_sha256 != master_sha256
            or transcript_master_build_id != master_build_id
        ):
            issues.append(
                _issue(
                    "stale_transcript",
                    "transcript.master_sha256",
                    "Транскрипт относится не к текущему мастер-файлу.",
                )
            )
        if not master_path.is_file() or sha256_file(master_path) != master_sha256:
            issues.append(
                _issue(
                    "master_checksum_mismatch",
                    "master.sha256",
                    "Мастер-файл отсутствует или не соответствует сохранённому SHA-256.",
                )
            )
        for item in files:
            if not (artifact_dir / item.filename).is_file():
                issues.append(
                    _issue(
                        "missing_required_file",
                        f"files.{item.role}",
                        "Обязательный файл комплекта отсутствует.",
                    )
                )
            try:
                ensure_safe_relative_path(item.local_relative_path)
                ensure_safe_relative_path(item.target_relative_path)
            except ValueError as exc:
                issues.append(_issue("unsafe_package_path", f"files.{item.role}", str(exc)))
            expected_base = base_name or files[0].filename.rsplit(".", 1)[0]
            if not item.filename.startswith(expected_base):
                issues.append(
                    _issue(
                        "base_name_mismatch",
                        f"files.{item.role}.filename",
                        "Базовые имена файлов комплекта не совпадают.",
                    )
                )
        if resolution == "unknown":
            issues.append(
                _issue(
                    "missing_video_stream",
                    "metadata.Resolution",
                    "У мастер-файла отсутствует видеопоток.",
                )
            )
        if metadata_status is not MetadataStatus.APPROVED:
            issues.append(
                _issue("metadata_not_approved", "metadata", "Метаданные не утверждены оператором.")
            )
        if metadata_category is not None and metadata_category not in self.schema.category_codes:
            issues.append(
                _issue(
                    "category_not_allowed",
                    "metadata.Category",
                    "Категория не разрешена схемой заказчика.",
                )
            )
        if self.schema.require_chapters and not has_chapters:
            issues.append(
                _issue(
                    "chapters_required",
                    "metadata.chapters",
                    "Схема заказчика требует хотя бы одну главу.",
                )
            )
        if analysis_review_status is not AnalysisReviewStatus.APPROVED:
            issues.append(
                _issue("analysis_not_approved", "analysis", "Анализ не утверждён оператором.")
            )
        if master_audio_decision_id != latest_audio_decision_id:
            issues.append(
                _issue(
                    "audio_policy_mismatch",
                    "master.audio_decision_id",
                    "Мастер собран не с последней аудиополитикой.",
                )
            )
        if audio_policy not in {value.value for value in self.schema.allowed_audio_policies}:
            issues.append(
                _issue(
                    "audio_policy_not_allowed",
                    "audio.policy",
                    "Выбранная аудиополитика запрещена схемой заказчика.",
                )
            )
        if synthetic_audio and not self.schema.allow_synthetic_voice:
            issues.append(
                _issue(
                    "synthetic_voice_not_allowed",
                    "audio.synthetic",
                    "Синтетический голос запрещён схемой заказчика.",
                )
            )
        if master_path.suffix.lower() not in self.schema.allowed_extensions:
            issues.append(
                _issue(
                    "container_not_allowed",
                    "master.container",
                    "Контейнер мастер-файла запрещён схемой заказчика.",
                )
            )
        if source_duplicate_of is not None:
            issues.append(
                _issue(
                    "exact_duplicate",
                    "source.duplicate_of",
                    "Исходник является точным дублем; исключение для поставки не зафиксировано.",
                )
            )
        try:
            self.decoder.validate(master_path)
        except Exception as exc:
            issues.append(
                _issue(
                    "master_decode_failed",
                    "master",
                    f"Мастер не прошёл повторную проверку декодирования: {exc}",
                )
            )
        return issues


def _json_file(role: str, path: Path) -> PackageFile:
    return PackageFile(
        role=role,
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        mime_type="application/json",
        local_relative_path=path.name,
        target_relative_path=path.name,
    )


def _materialize_master(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)


def _master_mime_type(path: Path) -> str:
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
    }.get(path.suffix.lower(), "application/octet-stream")


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=Severity.ERROR,
        path=path,
        message=message,
        blocking=True,
    )
