from contextvars import ContextVar, Token
from dataclasses import dataclass

from media_factory.domain.audit import Principal, UserRole


@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    correlation_id: str


_context: ContextVar[RequestContext | None] = ContextVar(
    "media_factory_request_context", default=None
)


def get_request_context() -> RequestContext:
    value = _context.get()
    if value is not None:
        return value
    return RequestContext(
        principal=Principal(actor="service-worker", role=UserRole.SERVICE),
        correlation_id="background",
    )


def set_request_context(value: RequestContext) -> Token[RequestContext | None]:
    return _context.set(value)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _context.reset(token)
