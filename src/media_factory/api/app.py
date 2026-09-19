from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status

from media_factory.config import Settings, get_settings
from media_factory.domain.analysis import AnalysisClip, AnalysisRun
from media_factory.domain.errors import (
    EntityNotFoundError,
    IdempotencyConflictError,
    VersionConflictError,
)
from media_factory.domain.job import Job, JobCreate
from media_factory.domain.models import StoredAsset, Transcript, ValidationIssue
from media_factory.domain.package import Package, PackageCreate, PackageTransitionRequest
from media_factory.domain.package_state import InvalidPackageTransition
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.asset_ingest import AssetIngestService
from media_factory.services.checksum import UploadTooLarge
from media_factory.services.job_queue import RedisJobQueue
from media_factory.services.job_service import JobDispatchError, JobService
from media_factory.services.media_inspector import FFprobeMediaInspector, MediaInspectionError
from media_factory.services.package_service import PackageService
from media_factory.services.transcript_validator import validate_transcript


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
) -> Job:
    try:
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


def _not_found(exc: EntityNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "not_found",
            "entity": exc.entity,
            "entity_id": exc.entity_id,
        },
    )
