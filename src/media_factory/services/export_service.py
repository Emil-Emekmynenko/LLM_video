import os
import shutil
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from media_factory.domain.package_state import PackageState
from media_factory.domain.packaging import PackageBuild, PackageBuildState
from media_factory.domain.qa import LocalExport, QADecision
from media_factory.persistence.export_repository import SQLAlchemyExportRepository
from media_factory.persistence.package_build_repository import (
    SQLAlchemyPackageBuildRepository,
)
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.qa_repository import SQLAlchemyQARepository
from media_factory.services.checksum import sha256_file
from media_factory.services.package_artifacts import ensure_safe_relative_path


class ExportWorkflowError(RuntimeError):
    code = "export_workflow_blocked"


class ExportAlreadyExists(RuntimeError):
    code = "export_already_exists"


class LocalExportService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        builds: SQLAlchemyPackageBuildRepository,
        reviews: SQLAlchemyQARepository,
        exports: SQLAlchemyExportRepository,
        export_dir: Path,
        chunk_size: int,
    ) -> None:
        self.packages = packages
        self.builds = builds
        self.reviews = reviews
        self.exports = exports
        self.export_dir = export_dir
        self.chunk_size = chunk_size

    def validate_request(self, package_id: str, package_build_id: str) -> None:
        package = self.packages.get(package_id)
        if package.state not in {PackageState.VALIDATED, PackageState.COMPLETE}:
            raise ExportWorkflowError("package must be validated or complete before export")
        build = self.builds.get(package_build_id)
        if build.package_id != package_id or build.state is not PackageBuildState.SUCCEEDED:
            raise ExportWorkflowError("package build is not exportable")
        review = self.reviews.get_for_build(package_build_id)
        if review.decision is not QADecision.APPROVED:
            raise ExportWorkflowError("package build has not been approved by QA")
        if build.manifest_sha256 != review.manifest_sha256:
            raise ExportWorkflowError("QA review and manifest SHA-256 do not match")

    def export(self, package_id: str, package_build_id: str) -> LocalExport:
        self.validate_request(package_id, package_build_id)
        build = self.builds.get(package_build_id)
        review = self.reviews.get_for_build(package_build_id)
        export = self.exports.create(
            package_id=package_id,
            package_build_id=package_build_id,
            qa_review_id=review.id,
        )
        temporary = self.export_dir / f".{export.id}.tmp"
        try:
            archive_name = f"{build.delivery_id}-v{build.version:04d}.zip"
            ensure_safe_relative_path(archive_name)
            if Path(archive_name).name != archive_name:
                raise ExportWorkflowError("export archive name is unsafe")
            destination = self.export_dir / archive_name
            self.export_dir.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ExportAlreadyExists(f"export already exists: {archive_name}")
            self._write_zip(temporary, build)
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise ExportAlreadyExists(f"export already exists: {archive_name}") from exc
            temporary.unlink()
            return self.exports.succeed(
                export.id,
                archive_path=destination,
                archive_size_bytes=destination.stat().st_size,
                archive_sha256=sha256_file(destination),
            )
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self.exports.fail(
                export.id,
                code=str(getattr(exc, "code", "local_export_failed")),
                message=str(exc),
            )
            raise

    def _write_zip(self, destination: Path, build: PackageBuild) -> None:
        if build.output_dir is None or build.manifest_path is None:
            raise ExportWorkflowError("package build artifacts are incomplete")
        output_dir = Path(build.output_dir)
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise ExportWorkflowError("package directory is missing or unsafe")
        resolved_output = output_dir.resolve()
        paths: list[tuple[Path, str, int | None, str]] = [
            (
                output_dir / item.local_relative_path,
                item.target_relative_path,
                item.size_bytes,
                item.sha256,
            )
            for item in build.files
        ]
        if build.manifest_sha256 is None:
            raise ExportWorkflowError("package manifest checksum is missing")
        paths.append(
            (
                Path(build.manifest_path),
                Path(build.manifest_path).name,
                None,
                build.manifest_sha256,
            )
        )
        with ZipFile(destination, mode="x", compression=ZIP_STORED, allowZip64=True) as archive:
            for source, archive_name, expected_size, expected_sha256 in paths:
                ensure_safe_relative_path(archive_name)
                if (
                    not source.is_file()
                    or source.is_symlink()
                    or not source.resolve().is_relative_to(resolved_output)
                ):
                    raise ExportWorkflowError(f"export source is missing or unsafe: {source.name}")
                if expected_size is not None and source.stat().st_size != expected_size:
                    raise ExportWorkflowError(f"export source size changed: {source.name}")
                if sha256_file(source) != expected_sha256:
                    raise ExportWorkflowError(f"export source checksum changed: {source.name}")
                info = ZipInfo(archive_name, date_time=_zip_timestamp(build.created_at))
                info.compress_type = ZIP_STORED
                info.external_attr = 0o100644 << 16
                with (
                    source.open("rb") as source_file,
                    archive.open(info, mode="w", force_zip64=True) as archive_file,
                ):
                    shutil.copyfileobj(source_file, archive_file, length=self.chunk_size)


def _zip_timestamp(value: datetime) -> tuple[int, int, int, int, int, int]:
    return (max(value.year, 1980), value.month, value.day, value.hour, value.minute, value.second)
