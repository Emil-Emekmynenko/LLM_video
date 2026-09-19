from media_factory.domain.package import Package
from media_factory.domain.package_state import PackageState, ensure_transition_allowed
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository


class GuardedPackageTransition(RuntimeError):
    def __init__(self, target: PackageState) -> None:
        super().__init__(f"Package state {target.value} is managed by its workflow service")
        self.target = target


class PackageService:
    def __init__(self, repository: SQLAlchemyPackageRepository) -> None:
        self.repository = repository

    def create(self, source_asset_id: str) -> Package:
        return self.repository.create(source_asset_id)

    def get(self, package_id: str) -> Package:
        return self.repository.get(package_id)

    def transition(
        self,
        package_id: str,
        *,
        target: PackageState,
        expected_version: int,
    ) -> Package:
        if target in {
            PackageState.GENERATING_NARRATION,
            PackageState.MASTER_BUILDING,
            PackageState.MASTER_FAILED,
            PackageState.MASTER_READY,
            PackageState.TRANSCRIBING,
            PackageState.TRANSCRIPTION_FAILED,
            PackageState.ALIGNING,
            PackageState.ALIGNMENT_FAILED,
            PackageState.PACKAGING,
            PackageState.VALIDATION_FAILED,
            PackageState.AWAITING_QA,
        }:
            raise GuardedPackageTransition(target)
        current = self.repository.get(package_id)
        if current.version != expected_version:
            from media_factory.domain.errors import VersionConflictError

            raise VersionConflictError("package", package_id, expected_version)
        ensure_transition_allowed(current.state, target)
        return self.repository.transition(
            package_id,
            target=target,
            expected_version=expected_version,
        )
