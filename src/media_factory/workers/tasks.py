from media_factory.config import get_settings
from media_factory.domain.job import JobKind
from media_factory.domain.package_state import PackageState
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.media_inspector import FFprobeMediaInspector, MediaInspectionError


def execute_job(job_id: str) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    jobs = SQLAlchemyJobRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    assets = SQLAlchemyAssetRepository(database.session_factory)
    job = jobs.mark_running(job_id)

    try:
        if job.kind is not JobKind.INSPECT_ASSET:
            raise UnsupportedJobKind(job.kind.value)
        package = packages.get(job.package_id)
        if package.state is not PackageState.INSPECTING:
            raise InvalidJobState(package.state.value, PackageState.INSPECTING.value)
        asset = assets.get(package.source_asset_id)
        inspector = FFprobeMediaInspector(ffprobe_bin=settings.ffprobe_bin)
        inspection = inspector.inspect(asset.stored_path)
        assets.update_inspection(asset.id, inspection)
        target = (
            PackageState.DUPLICATE_REVIEW
            if asset.duplicate_of is not None
            else PackageState.READY_FOR_ANALYSIS
        )
        packages.transition(package.id, target=target, expected_version=package.version)
        jobs.mark_succeeded(job.id)
    except MediaInspectionError as exc:
        jobs.mark_failed(job.id, code=exc.code, message=str(exc))
        raise
    except Exception as exc:
        jobs.mark_failed(job.id, code="job_failed", message=str(exc))
        raise


class UnsupportedJobKind(RuntimeError):
    pass


class InvalidJobState(RuntimeError):
    def __init__(self, current: str, required: str) -> None:
        super().__init__(f"Job requires package state {required}, current state is {current}")
