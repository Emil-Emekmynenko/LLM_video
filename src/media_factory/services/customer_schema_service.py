import json
from importlib.resources import files
from pathlib import Path

from media_factory.domain.customer_schema import CustomerSchema


class CustomerSchemaNotFound(RuntimeError):
    code = "customer_schema_not_found"


def load_customer_schema(
    customer: str,
    version: str,
    *,
    schema_dir: Path | None = None,
) -> CustomerSchema:
    if not _safe_component(customer) or not _safe_component(version):
        raise CustomerSchemaNotFound("unsafe customer schema identifier")
    if schema_dir is None:
        resource = files("media_factory").joinpath("schemas", customer, f"{version}.json")
        if not resource.is_file():
            raise CustomerSchemaNotFound(f"customer schema {customer}@{version} not found")
        payload = json.loads(resource.read_text(encoding="utf-8"))
    else:
        root = schema_dir.resolve()
        path = (root / customer / f"{version}.json").resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise CustomerSchemaNotFound(f"customer schema {customer}@{version} not found")
        payload = json.loads(path.read_text(encoding="utf-8"))
    schema = CustomerSchema.model_validate(payload)
    if schema.customer != customer or schema.version != version:
        raise CustomerSchemaNotFound("customer schema identity does not match its path")
    return schema


def _safe_component(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value
