import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from media_factory.services.customer_schema_service import (
    CustomerSchemaNotFound,
    load_customer_schema,
)
from media_factory.services.package_artifacts import (
    ordered_metadata,
    semantic_package_base_name,
)


def test_built_in_customer_schema_is_versioned_and_complete() -> None:
    schema = load_customer_schema("internal", "1.0")

    assert schema.customer == "internal"
    assert schema.version == "1.0"
    assert schema.required_files == ["master", "transcript", "metadata", "manifest"]
    assert schema.allowed_extensions == [".mp4"]


def test_external_schema_identity_must_match_path(tmp_path: Path) -> None:
    directory = tmp_path / "schemas" / "customer-a"
    directory.mkdir(parents=True)
    built_in = load_customer_schema("internal", "1.0").model_dump(mode="json")
    (directory / "1.0.json").write_text(json.dumps(built_in))

    with pytest.raises(CustomerSchemaNotFound):
        load_customer_schema("customer-a", "1.0", schema_dir=tmp_path / "schemas")


def test_schema_rejects_incomplete_metadata_order(tmp_path: Path) -> None:
    directory = tmp_path / "schemas" / "internal"
    directory.mkdir(parents=True)
    payload = load_customer_schema("internal", "1.0").model_dump(mode="json")
    payload["metadata_key_order"] = ["Title"]
    (directory / "1.0.json").write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_customer_schema("internal", "1.0", schema_dir=tmp_path / "schemas")


def test_metadata_order_and_semantic_base_name_are_deterministic() -> None:
    payload = {"Description": "d", "Title": "t"}

    assert list(ordered_metadata(payload, ["Title", "Description"])) == [
        "Title",
        "Description",
    ]
    assert semantic_package_base_name("Ремонт", "Fixing table leg", "package-1") == (
        "Fixing_table_leg"
    )
