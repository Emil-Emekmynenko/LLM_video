import pytest

from media_factory.domain.package_state import (
    InvalidPackageTransition,
    PackageState,
    ensure_transition_allowed,
)


def test_happy_path_transition_is_allowed() -> None:
    ensure_transition_allowed(PackageState.UPLOADED, PackageState.INSPECTING)


def test_pipeline_cannot_skip_validation_and_delivery() -> None:
    with pytest.raises(InvalidPackageTransition):
        ensure_transition_allowed(PackageState.PACKAGING, PackageState.COMPLETE)


def test_complete_is_terminal() -> None:
    with pytest.raises(InvalidPackageTransition):
        ensure_transition_allowed(PackageState.COMPLETE, PackageState.DRAFT)

