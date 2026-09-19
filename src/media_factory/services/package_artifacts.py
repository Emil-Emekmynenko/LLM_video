import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from media_factory.domain.models import Transcript

SAFE_BASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")


def safe_package_base_name(package_id: str) -> str:
    candidate = f"asset-{package_id}"
    if not SAFE_BASE_NAME.fullmatch(candidate):
        raise UnsafePackagePath("package id cannot form a safe base name")
    return candidate


def ensure_safe_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise UnsafePackagePath(f"unsafe relative path: {value}")


def deterministic_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def write_deterministic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(deterministic_json_bytes(value))


def calculate_wpm(transcript: Transcript) -> float:
    if transcript.media_duration <= 0:
        raise ValueError("media duration must be positive for WPM calculation")
    word_count = sum(len(segment.words) for segment in transcript.segments)
    return round(word_count / (transcript.media_duration / 60), 2)


class UnsafePackagePath(ValueError):
    code = "unsafe_package_path"
