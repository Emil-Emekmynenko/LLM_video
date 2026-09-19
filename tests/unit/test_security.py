import pytest
from pydantic import SecretStr

from media_factory.api.security import (
    AuthenticationError,
    PermissionDeniedError,
    authenticate,
    authorize,
)
from media_factory.config import Settings
from media_factory.domain.audit import Principal, UserRole


def auth_settings() -> Settings:
    return Settings(
        auth_enabled=True,
        operator_api_key=SecretStr("operator-secret"),
        qa_api_key=SecretStr("qa-secret"),
        admin_api_key=SecretStr("admin-secret"),
    )


def test_api_keys_resolve_to_least_privilege_roles() -> None:
    settings = auth_settings()

    operator = authenticate(settings, "operator-secret")
    qa = authenticate(settings, "qa-secret")
    admin = authenticate(settings, "admin-secret")

    assert operator.role is UserRole.OPERATOR
    assert qa.role is UserRole.QA
    assert admin.role is UserRole.ADMIN
    with pytest.raises(AuthenticationError):
        authenticate(settings, "wrong")


def test_qa_approval_is_role_protected() -> None:
    route = "/api/v1/packages/package-1/approve"

    authorize(Principal(actor="qa", role=UserRole.QA), "POST", route)
    authorize(Principal(actor="admin", role=UserRole.ADMIN), "POST", route)
    with pytest.raises(PermissionDeniedError):
        authorize(Principal(actor="operator", role=UserRole.OPERATOR), "POST", route)


def test_qa_cannot_start_operator_jobs() -> None:
    with pytest.raises(PermissionDeniedError):
        authorize(
            Principal(actor="qa", role=UserRole.QA),
            "POST",
            "/api/v1/packages/package-1/jobs",
        )
