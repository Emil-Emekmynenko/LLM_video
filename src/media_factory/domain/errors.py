class EntityNotFoundError(LookupError):
    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} {entity_id} was not found")
        self.entity = entity
        self.entity_id = entity_id


class VersionConflictError(RuntimeError):
    def __init__(self, entity: str, entity_id: str, expected_version: int) -> None:
        super().__init__(
            f"{entity} {entity_id} no longer has expected version {expected_version}"
        )
        self.entity = entity
        self.entity_id = entity_id
        self.expected_version = expected_version


class IdempotencyConflictError(RuntimeError):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(f"Idempotency key {idempotency_key} was used for another request")
        self.idempotency_key = idempotency_key
