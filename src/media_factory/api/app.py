from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status

from media_factory.config import Settings, get_settings
from media_factory.domain.models import StoredAsset, Transcript, ValidationIssue
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.services.asset_ingest import AssetIngestService
from media_factory.services.checksum import UploadTooLarge
from media_factory.services.media_inspector import FFprobeMediaInspector, MediaInspectionError
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


@app.get("/api/v1/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/ready")
def ready(
    inspector: FFprobeMediaInspector = Depends(get_inspector),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    ffprobe_available = inspector.is_available()
    database_available = database.is_available()
    is_ready = ffprobe_available and database_available
    return {
        "status": "ready" if is_ready else "not_ready",
        "checks": {
            "database": database_available,
            "ffprobe": ffprobe_available,
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
