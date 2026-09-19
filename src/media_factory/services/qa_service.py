import json
from datetime import UTC, datetime
from pathlib import Path

from media_factory.domain.package_state import (
    PackageState,
    ensure_transition_allowed,
)
from media_factory.domain.packaging import PackageBuild, PackageBuildState
from media_factory.domain.qa import QADecision, QAReview, QAReviewRequest
from media_factory.persistence.package_build_repository import (
    SQLAlchemyPackageBuildRepository,
)
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.qa_repository import SQLAlchemyQARepository
from media_factory.services.checksum import sha256_file
from media_factory.services.package_artifacts import (
    deterministic_json_bytes,
    ensure_safe_relative_path,
)


class QAWorkflowError(RuntimeError):
    code = "qa_workflow_blocked"


class QAArtifactMismatch(RuntimeError):
    code = "qa_artifact_mismatch"


class QAService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        builds: SQLAlchemyPackageBuildRepository,
        reviews: SQLAlchemyQARepository,
    ) -> None:
        self.packages = packages
        self.builds = builds
        self.reviews = reviews

    def review(self, package_id: str, request: QAReviewRequest) -> QAReview:
        package = self.packages.get(package_id)
        if package.state is not PackageState.AWAITING_QA:
            raise QAWorkflowError("package is not awaiting QA")
        build = self.builds.get(request.package_build_id)
        if build.package_id != package_id:
            raise QAWorkflowError("package build belongs to another package")
        if build.state is not PackageBuildState.SUCCEEDED or build.validation_issues:
            raise QAWorkflowError("package build has not passed automatic validation")
        package_builds = self.builds.list_for_package(package_id)
        if not package_builds or package_builds[-1].id != build.id:
            raise QAWorkflowError("only the latest package build can be reviewed")
        if build.output_dir is None or build.manifest_path is None or build.manifest_sha256 is None:
            raise QAWorkflowError("package build artifacts are incomplete")

        output_dir = Path(build.output_dir)
        manifest_path = Path(build.manifest_path)
        self._verify_artifacts(build, output_dir, manifest_path)
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        reviewed_at = datetime.now(UTC)
        decision = QADecision.APPROVED if request.approved else QADecision.REJECTED
        manifest["approvers"]["qa"] = request.reviewer
        manifest["qa_review"] = {
            "decision": decision.value,
            "reviewer": request.reviewer,
            "reason": request.reason,
            "reviewed_at": reviewed_at.isoformat(),
        }
        temporary_manifest = manifest_path.with_suffix(".json.tmp")
        temporary_manifest.write_bytes(deterministic_json_bytes(manifest))
        temporary_manifest.replace(manifest_path)
        final_manifest_sha256 = sha256_file(manifest_path)
        try:
            target_state = (
                PackageState.VALIDATED
                if request.approved
                else PackageState.VALIDATION_FAILED
            )
            ensure_transition_allowed(package.state, target_state)
            return self.reviews.create_and_finalize(
                package_id=package_id,
                package_build_id=build.id,
                decision=decision,
                reviewer=request.reviewer,
                reason=request.reason,
                manifest_sha256=final_manifest_sha256,
                reviewed_at=reviewed_at,
                expected_package_version=package.version,
                target_state=target_state,
            )
        except Exception:
            manifest_path.write_bytes(original_manifest)
            raise

    @staticmethod
    def _verify_artifacts(
        build: PackageBuild,
        output_dir: Path,
        manifest_path: Path,
    ) -> None:
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise QAArtifactMismatch("package directory is missing or unsafe")
        resolved_output = output_dir.resolve()
        if (
            not manifest_path.is_file()
            or manifest_path.is_symlink()
            or manifest_path.parent.resolve() != resolved_output
        ):
            raise QAArtifactMismatch("package directory or manifest is missing")
        if sha256_file(manifest_path) != build.manifest_sha256:
            raise QAArtifactMismatch("manifest SHA-256 changed after package build")
        for item in build.files:
            ensure_safe_relative_path(item.local_relative_path)
            path = output_dir / item.local_relative_path
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(resolved_output)
            ):
                raise QAArtifactMismatch(f"package file is missing or unsafe: {item.filename}")
            if path.stat().st_size != item.size_bytes or sha256_file(path) != item.sha256:
                raise QAArtifactMismatch(f"package file changed after validation: {item.filename}")
