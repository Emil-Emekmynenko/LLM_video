import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from media_factory.config import Settings, get_settings
from media_factory.domain.analysis import (
    AnalysisClip,
    AnalysisReviewRequest,
    AnalysisRun,
    EventReviewRequest,
    TimelineEvent,
)
from media_factory.domain.delivery import (
    DeliveryAttempt,
    DeliveryCreateRequest,
    UploadedObject,
)
from media_factory.domain.errors import (
    EntityNotFoundError,
    IdempotencyConflictError,
    VersionConflictError,
)
from media_factory.domain.job import Job, JobCreate, JobKind
from media_factory.domain.master import MasterBuild
from media_factory.domain.metadata import (
    MetadataApprovalRequest,
    MetadataCategory,
    MetadataProposalRequest,
    MetadataRevisionRequest,
    MetadataVersion,
)
from media_factory.domain.models import StoredAsset, Transcript, ValidationIssue
from media_factory.domain.narration import (
    AudioDecision,
    AudioDecisionRequest,
    AudioTrack,
    NarrationApprovalRequest,
    NarrationProposalRequest,
    NarrationRevisionRequest,
    NarrationScript,
    TTSRun,
)
from media_factory.domain.package import Package, PackageCreate, PackageTransitionRequest
from media_factory.domain.package_state import InvalidPackageTransition
from media_factory.domain.packaging import PackageBuild
from media_factory.domain.qa import ExportState, LocalExport, QAReview, QAReviewRequest
from media_factory.domain.transcription import TranscriptionRun
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.delivery_repository import (
    DeliveryStateConflict,
    SQLAlchemyDeliveryRepository,
)
from media_factory.persistence.export_repository import SQLAlchemyExportRepository
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.master_repository import SQLAlchemyMasterRepository
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_build_repository import (
    SQLAlchemyPackageBuildRepository,
)
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.qa_repository import (
    QAReviewAlreadyExists,
    SQLAlchemyQARepository,
)
from media_factory.persistence.transcription_repository import (
    SQLAlchemyTranscriptionRepository,
)
from media_factory.providers.storage_factory import (
    build_checkpoint_cipher,
    build_object_storage_provider,
)
from media_factory.services.analysis_review import AnalysisReviewError, AnalysisReviewService
from media_factory.services.asset_ingest import AssetIngestService
from media_factory.services.checksum import UploadTooLarge
from media_factory.services.delivery_service import DeliveryService, DeliveryWorkflowError
from media_factory.services.export_service import ExportWorkflowError, LocalExportService
from media_factory.services.job_queue import RedisJobQueue
from media_factory.services.job_service import JobDispatchError, JobService
from media_factory.services.master_processing import (
    FFmpegDecodeValidator,
    FFmpegMasterAssembler,
)
from media_factory.services.master_service import MasterService, MasterWorkflowError
from media_factory.services.media_inspector import FFprobeMediaInspector, MediaInspectionError
from media_factory.services.metadata_service import MetadataService, MetadataWorkflowError
from media_factory.services.narration_service import NarrationService, NarrationWorkflowError
from media_factory.services.package_service import GuardedPackageTransition, PackageService
from media_factory.services.packaging_service import (
    PackagingService,
    PackagingWorkflowError,
)
from media_factory.services.qa_service import QAArtifactMismatch, QAService, QAWorkflowError
from media_factory.services.transcript_validator import validate_transcript
from media_factory.services.transcription_service import (
    TranscriptionService,
    TranscriptionWorkflowError,
)


@lru_cache(maxsize=1)
def get_database() -> Database:
    return Database(get_settings().database_url)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.auto_create_schema:
        get_database().create_schema()
    yield


app = FastAPI(
    title="Media Dataset Factory",
    version="0.1.0",
    description="Prepare, validate, package, and deliver video assets.",
    lifespan=lifespan,
)


def get_inspector(settings: Settings = Depends(get_settings)) -> FFprobeMediaInspector:
    return FFprobeMediaInspector(ffprobe_bin=settings.ffprobe_bin)


@lru_cache(maxsize=1)
def get_job_queue() -> RedisJobQueue:
    settings = get_settings()
    return RedisJobQueue(settings.redis_url, settings.queue_name)


def get_ingest_service(
    settings: Settings = Depends(get_settings),
    inspector: FFprobeMediaInspector = Depends(get_inspector),
    database: Database = Depends(get_database),
) -> AssetIngestService:
    return AssetIngestService(
        upload_dir=settings.upload_dir,
        max_upload_bytes=settings.max_upload_bytes,
        inspector=inspector,
        repository=SQLAlchemyAssetRepository(database.session_factory),
    )


def get_package_service(database: Database = Depends(get_database)) -> PackageService:
    return PackageService(SQLAlchemyPackageRepository(database.session_factory))


def get_job_service(
    database: Database = Depends(get_database),
    queue: RedisJobQueue = Depends(get_job_queue),
) -> JobService:
    return JobService(SQLAlchemyJobRepository(database.session_factory), queue)


def get_analysis_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyAnalysisRepository:
    return SQLAlchemyAnalysisRepository(database.session_factory)


def get_metadata_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyMetadataRepository:
    return SQLAlchemyMetadataRepository(database.session_factory)


def get_analysis_review_service(
    database: Database = Depends(get_database),
    repository: SQLAlchemyAnalysisRepository = Depends(get_analysis_repository),
) -> AnalysisReviewService:
    return AnalysisReviewService(
        repository,
        SQLAlchemyPackageRepository(database.session_factory),
    )


def get_metadata_service(
    database: Database = Depends(get_database),
    analyses: SQLAlchemyAnalysisRepository = Depends(get_analysis_repository),
    metadata: SQLAlchemyMetadataRepository = Depends(get_metadata_repository),
) -> MetadataService:
    return MetadataService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        analyses=analyses,
        metadata=metadata,
    )


def get_narration_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyNarrationRepository:
    return SQLAlchemyNarrationRepository(database.session_factory)


def get_narration_service(
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    metadata: SQLAlchemyMetadataRepository = Depends(get_metadata_repository),
    narration: SQLAlchemyNarrationRepository = Depends(get_narration_repository),
) -> NarrationService:
    return NarrationService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        metadata=metadata,
        narration=narration,
        narration_dir=settings.narration_dir,
        allow_additional_audio=settings.allow_additional_audio,
    )


def get_master_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyMasterRepository:
    return SQLAlchemyMasterRepository(database.session_factory)


def get_master_service(
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    inspector: FFprobeMediaInspector = Depends(get_inspector),
    masters: SQLAlchemyMasterRepository = Depends(get_master_repository),
    narration: SQLAlchemyNarrationRepository = Depends(get_narration_repository),
) -> MasterService:
    return MasterService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        assets=SQLAlchemyAssetRepository(database.session_factory),
        narration=narration,
        masters=masters,
        assembler=FFmpegMasterAssembler(ffmpeg_bin=settings.ffmpeg_bin),
        decoder=FFmpegDecodeValidator(ffmpeg_bin=settings.ffmpeg_bin),
        inspector=inspector,
        master_dir=settings.master_dir,
        duration_tolerance=settings.master_duration_tolerance,
        container_extension=settings.master_container_extension,
    )


def get_transcription_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyTranscriptionRepository:
    return SQLAlchemyTranscriptionRepository(database.session_factory)


def get_transcription_service(
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    masters: SQLAlchemyMasterRepository = Depends(get_master_repository),
    transcriptions: SQLAlchemyTranscriptionRepository = Depends(
        get_transcription_repository
    ),
) -> TranscriptionService:
    return TranscriptionService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        masters=masters,
        transcriptions=transcriptions,
        duration_tolerance=settings.transcript_duration_tolerance,
    )


def get_package_build_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyPackageBuildRepository:
    return SQLAlchemyPackageBuildRepository(database.session_factory)


def get_packaging_service(
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    builds: SQLAlchemyPackageBuildRepository = Depends(get_package_build_repository),
) -> PackagingService:
    return PackagingService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        assets=SQLAlchemyAssetRepository(database.session_factory),
        analyses=SQLAlchemyAnalysisRepository(database.session_factory),
        metadata=SQLAlchemyMetadataRepository(database.session_factory),
        narration=SQLAlchemyNarrationRepository(database.session_factory),
        masters=SQLAlchemyMasterRepository(database.session_factory),
        transcriptions=SQLAlchemyTranscriptionRepository(database.session_factory),
        builds=builds,
        decoder=FFmpegDecodeValidator(ffmpeg_bin=settings.ffmpeg_bin),
        package_dir=settings.package_dir,
        customer=settings.default_customer,
        schema_version=settings.default_customer_schema_version,
        duration_tolerance=settings.transcript_duration_tolerance,
    )


def get_qa_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyQARepository:
    return SQLAlchemyQARepository(database.session_factory)


def get_qa_service(
    database: Database = Depends(get_database),
    builds: SQLAlchemyPackageBuildRepository = Depends(get_package_build_repository),
    reviews: SQLAlchemyQARepository = Depends(get_qa_repository),
) -> QAService:
    return QAService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        builds=builds,
        reviews=reviews,
    )


def get_export_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyExportRepository:
    return SQLAlchemyExportRepository(database.session_factory)


def get_export_service(
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    builds: SQLAlchemyPackageBuildRepository = Depends(get_package_build_repository),
    reviews: SQLAlchemyQARepository = Depends(get_qa_repository),
    exports: SQLAlchemyExportRepository = Depends(get_export_repository),
) -> LocalExportService:
    return LocalExportService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        builds=builds,
        reviews=reviews,
        exports=exports,
        export_dir=settings.export_dir,
        chunk_size=settings.export_chunk_size,
    )


def get_delivery_repository(
    database: Database = Depends(get_database),
) -> SQLAlchemyDeliveryRepository:
    return SQLAlchemyDeliveryRepository(database.session_factory)


def get_delivery_service(
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    builds: SQLAlchemyPackageBuildRepository = Depends(get_package_build_repository),
    reviews: SQLAlchemyQARepository = Depends(get_qa_repository),
) -> DeliveryService:
    deliveries = SQLAlchemyDeliveryRepository(
        database.session_factory,
        checkpoint_cipher=build_checkpoint_cipher(settings),
    )
    return DeliveryService(
        packages=SQLAlchemyPackageRepository(database.session_factory),
        builds=builds,
        reviews=reviews,
        deliveries=deliveries,
        provider=build_object_storage_provider(settings),
    )


@app.get("/api/v1/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/ready")
def ready(
    inspector: FFprobeMediaInspector = Depends(get_inspector),
    database: Database = Depends(get_database),
    queue: RedisJobQueue = Depends(get_job_queue),
) -> dict[str, Any]:
    ffprobe_available = inspector.is_available()
    database_available = database.is_available()
    redis_available = queue.is_available()
    is_ready = ffprobe_available and database_available and redis_available
    return {
        "status": "ready" if is_ready else "not_ready",
        "checks": {
            "database": database_available,
            "ffprobe": ffprobe_available,
            "redis": redis_available,
        },
    }


@app.post(
    "/api/v1/assets/uploads",
    response_model=StoredAsset,
    status_code=status.HTTP_201_CREATED,
)
def upload_asset(
    file: UploadFile = File(...),
    service: AssetIngestService = Depends(get_ingest_service),
) -> StoredAsset:
    try:
        return service.ingest(file.file, file.filename or "upload.bin")
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "upload_too_large", "max_bytes": exc.max_bytes},
        ) from exc
    except MediaInspectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@app.post("/api/v1/transcripts/validate", response_model=list[ValidationIssue])
def validate_transcript_endpoint(transcript: Transcript) -> list[ValidationIssue]:
    return validate_transcript(transcript)


@app.post(
    "/api/v1/packages",
    response_model=Package,
    status_code=status.HTTP_201_CREATED,
)
def create_package(
    request: PackageCreate,
    service: PackageService = Depends(get_package_service),
) -> Package:
    try:
        return service.create(request.source_asset_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.get("/api/v1/packages/{package_id}", response_model=Package)
def get_package(
    package_id: str,
    service: PackageService = Depends(get_package_service),
) -> Package:
    try:
        return service.get(package_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.post("/api/v1/packages/{package_id}/transitions", response_model=Package)
def transition_package(
    package_id: str,
    request: PackageTransitionRequest,
    service: PackageService = Depends(get_package_service),
) -> Package:
    try:
        return service.transition(
            package_id,
            target=request.target,
            expected_version=request.expected_version,
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "version_conflict",
                "entity": exc.entity,
                "entity_id": exc.entity_id,
                "expected_version": exc.expected_version,
            },
        ) from exc
    except InvalidPackageTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_package_transition",
                "current": exc.current.value,
                "target": exc.target.value,
            },
        ) from exc
    except GuardedPackageTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "system_managed_transition",
                "target": exc.target.value,
            },
        ) from exc


@app.post(
    "/api/v1/packages/{package_id}/jobs",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_job(
    package_id: str,
    request: JobCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    service: JobService = Depends(get_job_service),
    narration: NarrationService = Depends(get_narration_service),
    masters: MasterService = Depends(get_master_service),
    transcriptions: TranscriptionService = Depends(get_transcription_service),
    packaging: PackagingService = Depends(get_packaging_service),
    exports: LocalExportService = Depends(get_export_service),
) -> Job:
    try:
        if request.kind is JobKind.GENERATE_NARRATION:
            script_id = request.payload.get("script_id")
            if not isinstance(script_id, str) or not script_id:
                raise NarrationWorkflowError("generate_narration requires script_id")
            narration.validate_tts_request(package_id, script_id)
        elif request.kind is JobKind.BUILD_MASTER:
            masters.validate_build_request(package_id)
        elif request.kind is JobKind.TRANSCRIBE_MASTER:
            transcriptions.validate_request(package_id)
        elif request.kind is JobKind.BUILD_PACKAGE:
            packaging.validate_request(package_id)
        elif request.kind is JobKind.EXPORT_PACKAGE:
            package_build_id = request.payload.get("package_build_id")
            if not isinstance(package_build_id, str) or not package_build_id:
                raise ExportWorkflowError("export_package requires package_build_id")
            exports.validate_request(package_id, package_build_id)
        elif request.kind is JobKind.DELIVER_PACKAGE:
            raise DeliveryWorkflowError("use the package delivery endpoint")
        return service.create(
            package_id=package_id,
            kind=request.kind,
            idempotency_key=idempotency_key,
            payload=request.payload,
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "idempotency_key": exc.idempotency_key,
            },
        ) from exc
    except JobDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "job_dispatch_failed", "message": str(exc)},
        ) from exc
    except NarrationWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc
    except MasterWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc
    except TranscriptionWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc
    except PackagingWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc
    except ExportWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc
    except DeliveryWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post(
    "/api/v1/packages/{package_id}/build-master",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def build_master(
    package_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    jobs: JobService = Depends(get_job_service),
    masters: MasterService = Depends(get_master_service),
) -> Job:
    try:
        masters.validate_build_request(package_id)
        return jobs.create(
            package_id=package_id,
            kind=JobKind.BUILD_MASTER,
            idempotency_key=idempotency_key,
            payload={},
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflictError as exc:
        raise _workflow_conflict("idempotency_conflict", str(exc)) from exc
    except JobDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "job_dispatch_failed", "message": str(exc)},
        ) from exc
    except MasterWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post(
    "/api/v1/packages/{package_id}/transcribe",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def transcribe_master(
    package_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    jobs: JobService = Depends(get_job_service),
    transcriptions: TranscriptionService = Depends(get_transcription_service),
) -> Job:
    try:
        transcriptions.validate_request(package_id)
        return jobs.create(
            package_id=package_id,
            kind=JobKind.TRANSCRIBE_MASTER,
            idempotency_key=idempotency_key,
            payload={},
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflictError as exc:
        raise _workflow_conflict("idempotency_conflict", str(exc)) from exc
    except JobDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "job_dispatch_failed", "message": str(exc)},
        ) from exc
    except TranscriptionWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post(
    "/api/v1/packages/{package_id}/build",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def build_package(
    package_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    jobs: JobService = Depends(get_job_service),
    packaging: PackagingService = Depends(get_packaging_service),
) -> Job:
    try:
        packaging.validate_request(package_id)
        return jobs.create(
            package_id=package_id,
            kind=JobKind.BUILD_PACKAGE,
            idempotency_key=idempotency_key,
            payload={},
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflictError as exc:
        raise _workflow_conflict("idempotency_conflict", str(exc)) from exc
    except JobDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "job_dispatch_failed", "message": str(exc)},
        ) from exc
    except PackagingWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post("/api/v1/packages/{package_id}/approve", response_model=QAReview)
def review_package(
    package_id: str,
    request: QAReviewRequest,
    service: QAService = Depends(get_qa_service),
) -> QAReview:
    try:
        return service.review(package_id, request)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except QAReviewAlreadyExists as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc
    except (QAWorkflowError, QAArtifactMismatch) as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post(
    "/api/v1/package-builds/{package_build_id}/exports",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_local_export(
    package_build_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    jobs: JobService = Depends(get_job_service),
    builds: SQLAlchemyPackageBuildRepository = Depends(get_package_build_repository),
    exports: LocalExportService = Depends(get_export_service),
) -> Job:
    try:
        build = builds.get(package_build_id)
        exports.validate_request(build.package_id, build.id)
        return jobs.create(
            package_id=build.package_id,
            kind=JobKind.EXPORT_PACKAGE,
            idempotency_key=idempotency_key,
            payload={"package_build_id": build.id},
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflictError as exc:
        raise _workflow_conflict("idempotency_conflict", str(exc)) from exc
    except JobDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "job_dispatch_failed", "message": str(exc)},
        ) from exc
    except ExportWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post(
    "/api/v1/packages/{package_id}/deliver",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_delivery(
    package_id: str,
    request: DeliveryCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    deliveries: DeliveryService = Depends(get_delivery_service),
    jobs: JobService = Depends(get_job_service),
) -> Job:
    try:
        delivery = deliveries.create(
            package_id,
            request,
            idempotency_key=idempotency_key,
        )
        return jobs.create(
            package_id=package_id,
            kind=JobKind.DELIVER_PACKAGE,
            idempotency_key=_delivery_job_key(delivery.id, idempotency_key),
            payload={"delivery_id": delivery.id},
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflictError as exc:
        raise _workflow_conflict("idempotency_conflict", str(exc)) from exc
    except JobDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "job_dispatch_failed", "message": str(exc)},
        ) from exc
    except (DeliveryWorkflowError, DeliveryStateConflict) as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post(
    "/api/v1/deliveries/{delivery_id}/retry",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_delivery(
    delivery_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    deliveries: DeliveryService = Depends(get_delivery_service),
    jobs: JobService = Depends(get_job_service),
) -> Job:
    try:
        delivery = deliveries.retry(delivery_id)
        return jobs.create(
            package_id=delivery.package_id,
            kind=JobKind.DELIVER_PACKAGE,
            idempotency_key=_delivery_job_key(delivery.id, idempotency_key),
            payload={"delivery_id": delivery.id},
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflictError as exc:
        raise _workflow_conflict("idempotency_conflict", str(exc)) from exc
    except JobDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "job_dispatch_failed", "message": str(exc)},
        ) from exc
    except (DeliveryWorkflowError, DeliveryStateConflict) as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.get("/api/v1/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, service: JobService = Depends(get_job_service)) -> Job:
    try:
        return service.get(job_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.get("/api/v1/analysis-runs/{run_id}", response_model=AnalysisRun)
def get_analysis_run(
    run_id: str,
    repository: SQLAlchemyAnalysisRepository = Depends(get_analysis_repository),
) -> AnalysisRun:
    try:
        return repository.get(run_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.get("/api/v1/analysis-runs/{run_id}/clips", response_model=list[AnalysisClip])
def list_analysis_clips(
    run_id: str,
    repository: SQLAlchemyAnalysisRepository = Depends(get_analysis_repository),
) -> list[AnalysisClip]:
    try:
        repository.get(run_id)
        return repository.list_clips(run_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.get("/api/v1/analysis-runs/{run_id}/events", response_model=list[TimelineEvent])
def list_analysis_events(
    run_id: str,
    repository: SQLAlchemyAnalysisRepository = Depends(get_analysis_repository),
) -> list[TimelineEvent]:
    try:
        repository.get(run_id)
        return repository.list_events(run_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.patch("/api/v1/analysis-events/{event_id}", response_model=TimelineEvent)
def review_analysis_event(
    event_id: str,
    request: EventReviewRequest,
    service: AnalysisReviewService = Depends(get_analysis_review_service),
) -> TimelineEvent:
    try:
        return service.review_event(
            event_id,
            review_status=request.review_status,
            expected_version=request.expected_version,
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except AnalysisReviewError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post("/api/v1/analysis-runs/{run_id}/reviews", response_model=AnalysisRun)
def review_analysis_run(
    run_id: str,
    request: AnalysisReviewRequest,
    service: AnalysisReviewService = Depends(get_analysis_review_service),
) -> AnalysisRun:
    try:
        return service.review_run(run_id, approved=request.approved)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except AnalysisReviewError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.get("/api/v1/metadata/categories", response_model=list[MetadataCategory])
def list_metadata_categories(
    repository: SQLAlchemyMetadataRepository = Depends(get_metadata_repository),
) -> list[MetadataCategory]:
    repository.ensure_default_categories()
    return repository.list_categories()


@app.post(
    "/api/v1/packages/{package_id}/metadata/proposals",
    response_model=MetadataVersion,
    status_code=status.HTTP_201_CREATED,
)
def propose_metadata(
    package_id: str,
    request: MetadataProposalRequest,
    service: MetadataService = Depends(get_metadata_service),
) -> MetadataVersion:
    try:
        return service.propose(package_id, request.analysis_run_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except MetadataWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.get(
    "/api/v1/packages/{package_id}/metadata",
    response_model=list[MetadataVersion],
)
def list_package_metadata(
    package_id: str,
    database: Database = Depends(get_database),
    repository: SQLAlchemyMetadataRepository = Depends(get_metadata_repository),
) -> list[MetadataVersion]:
    try:
        SQLAlchemyPackageRepository(database.session_factory).get(package_id)
        return repository.list_for_package(package_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.post(
    "/api/v1/metadata/{metadata_id}/revisions",
    response_model=MetadataVersion,
    status_code=status.HTTP_201_CREATED,
)
def revise_metadata(
    metadata_id: str,
    request: MetadataRevisionRequest,
    service: MetadataService = Depends(get_metadata_service),
) -> MetadataVersion:
    try:
        return service.revise(metadata_id, request)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except MetadataWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post("/api/v1/metadata/{metadata_id}/approve", response_model=MetadataVersion)
def approve_metadata(
    metadata_id: str,
    request: MetadataApprovalRequest,
    service: MetadataService = Depends(get_metadata_service),
) -> MetadataVersion:
    try:
        return service.approve(metadata_id, expected_version=request.expected_version)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except MetadataWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post(
    "/api/v1/packages/{package_id}/narration/proposals",
    response_model=NarrationScript,
    status_code=status.HTTP_201_CREATED,
)
def propose_narration(
    package_id: str,
    request: NarrationProposalRequest,
    service: NarrationService = Depends(get_narration_service),
) -> NarrationScript:
    try:
        return service.propose_script(package_id, request)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except NarrationWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.get(
    "/api/v1/packages/{package_id}/narration",
    response_model=list[NarrationScript],
)
def list_narration_scripts(
    package_id: str,
    database: Database = Depends(get_database),
    repository: SQLAlchemyNarrationRepository = Depends(get_narration_repository),
) -> list[NarrationScript]:
    try:
        SQLAlchemyPackageRepository(database.session_factory).get(package_id)
        return repository.list_scripts(package_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.post(
    "/api/v1/narration/{script_id}/revisions",
    response_model=NarrationScript,
    status_code=status.HTTP_201_CREATED,
)
def revise_narration(
    script_id: str,
    request: NarrationRevisionRequest,
    service: NarrationService = Depends(get_narration_service),
) -> NarrationScript:
    try:
        return service.revise_script(script_id, request)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except NarrationWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.post("/api/v1/narration/{script_id}/approve", response_model=NarrationScript)
def approve_narration(
    script_id: str,
    request: NarrationApprovalRequest,
    service: NarrationService = Depends(get_narration_service),
) -> NarrationScript:
    try:
        return service.approve_script(script_id, expected_version=request.expected_version)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except NarrationWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.get(
    "/api/v1/packages/{package_id}/audio-tracks",
    response_model=list[AudioTrack],
)
def list_audio_tracks(
    package_id: str,
    repository: SQLAlchemyNarrationRepository = Depends(get_narration_repository),
) -> list[AudioTrack]:
    return repository.list_audio_tracks(package_id)


@app.get(
    "/api/v1/packages/{package_id}/tts-runs",
    response_model=list[TTSRun],
)
def list_tts_runs(
    package_id: str,
    repository: SQLAlchemyNarrationRepository = Depends(get_narration_repository),
) -> list[TTSRun]:
    return repository.list_tts_runs(package_id)


@app.post(
    "/api/v1/packages/{package_id}/audio-decisions",
    response_model=AudioDecision,
    status_code=status.HTTP_201_CREATED,
)
def decide_audio(
    package_id: str,
    request: AudioDecisionRequest,
    service: NarrationService = Depends(get_narration_service),
) -> AudioDecision:
    try:
        return service.decide_audio(package_id, request)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except NarrationWorkflowError as exc:
        raise _workflow_conflict(exc.code, str(exc)) from exc


@app.get(
    "/api/v1/packages/{package_id}/audio-decisions",
    response_model=list[AudioDecision],
)
def list_audio_decisions(
    package_id: str,
    repository: SQLAlchemyNarrationRepository = Depends(get_narration_repository),
) -> list[AudioDecision]:
    return repository.list_audio_decisions(package_id)


@app.get(
    "/api/v1/packages/{package_id}/master-builds",
    response_model=list[MasterBuild],
)
def list_master_builds(
    package_id: str,
    repository: SQLAlchemyMasterRepository = Depends(get_master_repository),
) -> list[MasterBuild]:
    return repository.list_for_package(package_id)


@app.get(
    "/api/v1/packages/{package_id}/transcription-runs",
    response_model=list[TranscriptionRun],
)
def list_transcription_runs(
    package_id: str,
    repository: SQLAlchemyTranscriptionRepository = Depends(
        get_transcription_repository
    ),
) -> list[TranscriptionRun]:
    return repository.list_for_package(package_id)


@app.get(
    "/api/v1/packages/{package_id}/builds",
    response_model=list[PackageBuild],
)
def list_package_builds(
    package_id: str,
    repository: SQLAlchemyPackageBuildRepository = Depends(
        get_package_build_repository
    ),
) -> list[PackageBuild]:
    return repository.list_for_package(package_id)


@app.get("/api/v1/deliveries", response_model=list[DeliveryAttempt])
def list_deliveries(
    package_id: str | None = None,
    repository: SQLAlchemyDeliveryRepository = Depends(get_delivery_repository),
) -> list[DeliveryAttempt]:
    return repository.list_attempts(package_id)


@app.get("/api/v1/deliveries/{delivery_id}", response_model=DeliveryAttempt)
def get_delivery(
    delivery_id: str,
    repository: SQLAlchemyDeliveryRepository = Depends(get_delivery_repository),
) -> DeliveryAttempt:
    try:
        return repository.get(delivery_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.get(
    "/api/v1/deliveries/{delivery_id}/objects",
    response_model=list[UploadedObject],
)
def list_delivery_objects(
    delivery_id: str,
    repository: SQLAlchemyDeliveryRepository = Depends(get_delivery_repository),
) -> list[UploadedObject]:
    try:
        repository.get(delivery_id)
        return repository.list_objects(delivery_id)
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


@app.get("/api/v1/packages/{package_id}/qa-reviews", response_model=list[QAReview])
def list_qa_reviews(
    package_id: str,
    repository: SQLAlchemyQARepository = Depends(get_qa_repository),
) -> list[QAReview]:
    return repository.list_for_package(package_id)


@app.get(
    "/api/v1/package-builds/{package_build_id}/exports",
    response_model=list[LocalExport],
)
def list_local_exports(
    package_build_id: str,
    repository: SQLAlchemyExportRepository = Depends(get_export_repository),
) -> list[LocalExport]:
    return repository.list_for_build(package_build_id)


@app.get("/api/v1/exports/{export_id}/download", response_class=FileResponse)
def download_local_export(
    export_id: str,
    repository: SQLAlchemyExportRepository = Depends(get_export_repository),
) -> FileResponse:
    try:
        export = repository.get(export_id)
        if export.state is not ExportState.SUCCEEDED or export.archive_path is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "export_not_ready"},
            )
        archive = Path(export.archive_path)
        if not archive.is_file() or archive.is_symlink():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "export_artifact_missing"},
            )
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=archive.name,
        )
    except EntityNotFoundError as exc:
        raise _not_found(exc) from exc


def _not_found(exc: EntityNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "not_found",
            "entity": exc.entity,
            "entity_id": exc.entity_id,
        },
    )


def _delivery_job_key(delivery_id: str, request_key: str) -> str:
    digest = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
    return f"delivery:{delivery_id}:{digest}"


def _version_conflict(exc: VersionConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "version_conflict",
            "entity": exc.entity,
            "entity_id": exc.entity_id,
            "expected_version": exc.expected_version,
        },
    )


def _workflow_conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )
