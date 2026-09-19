import json

import pytest

from media_factory.domain.models import Transcript, TranscriptSegment, TranscriptWord
from media_factory.services.package_artifacts import (
    UnsafePackagePath,
    calculate_wpm,
    deterministic_json_bytes,
    ensure_safe_relative_path,
    safe_package_base_name,
)


def test_wpm_uses_full_master_duration() -> None:
    transcript = Transcript(
        asset_id="asset-1",
        master_sha256="a" * 64,
        media_duration=120,
        segments=[
            TranscriptSegment(
                start=0,
                end=2,
                text="one two three four",
                words=[
                    TranscriptWord(word=word, start=index * 0.2, end=index * 0.2 + 0.1)
                    for index, word in enumerate(("one", "two", "three", "four"))
                ],
            )
        ],
    )

    assert calculate_wpm(transcript) == 2.0


def test_metadata_json_preserves_declared_key_order() -> None:
    payload = {"Title": "Example", "Description": "Description", "Duration": 1.0}

    encoded = deterministic_json_bytes(payload)

    assert list(json.loads(encoded).keys()) == ["Title", "Description", "Duration"]
    assert encoded == deterministic_json_bytes(payload)


def test_package_paths_are_safe() -> None:
    assert safe_package_base_name("0199-example") == "asset-0199-example"
    ensure_safe_relative_path("asset-0199-example.metadata.json")

    with pytest.raises(UnsafePackagePath):
        ensure_safe_relative_path("../outside.json")
