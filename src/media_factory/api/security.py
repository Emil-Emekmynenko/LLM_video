import secrets

from media_factory.config import Settings
from media_factory.domain.audit import Principal, UserRole


class AuthenticationError(RuntimeError):
    pass


class PermissionDeniedError(RuntimeError):
    pass


def authenticate(settings: Settings, api_key: str | None) -> Principal:
    if not settings.auth_enabled:
        return Principal(actor="local-admin", role=UserRole.ADMIN)
    if not api_key:
        raise AuthenticationError("X-API-Key header is required")
    candidates = (
        (UserRole.OPERATOR, settings.operator_api_key.get_secret_value()),
        (UserRole.QA, settings.qa_api_key.get_secret_value()),
        (UserRole.ADMIN, settings.admin_api_key.get_secret_value()),
    )
    for role, expected in candidates:
        if expected and secrets.compare_digest(api_key, expected):
            return Principal(actor=f"{role.value}-api-key", role=role)
    raise AuthenticationError("invalid API key")


def authorize(principal: Principal, method: str, route_path: str) -> None:
    if method in {"GET", "HEAD", "OPTIONS"}:
        if route_path == "/api/v1/audit-events" and principal.role is not UserRole.ADMIN:
            raise PermissionDeniedError("global audit requires the admin role")
        return
    if route_path.startswith("/api/v1/packages/") and route_path.endswith("/approve"):
        if principal.role not in {UserRole.QA, UserRole.ADMIN}:
            raise PermissionDeniedError("QA approval requires the qa or admin role")
        return
    if principal.role not in {UserRole.OPERATOR, UserRole.ADMIN}:
        raise PermissionDeniedError("this operation requires the operator or admin role")
