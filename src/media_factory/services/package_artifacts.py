import json
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from media_factory.domain.models import Transcript

SAFE_BASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")


def safe_package_base_name(package_id: str) -> str:
    candidate = f"asset-{package_id}"
    if not SAFE_BASE_NAME.fullmatch(candidate):
        raise UnsafePackagePath("package id cannot form a safe base name")
    return candidate


def semantic_package_base_name(category: str, title: str, package_id: str) -> str:
    category_part = _ascii_slug(category)
    title_part = _ascii_slug(title)
    candidate = "_".join(part for part in (category_part, title_part) if part)[:200]
    if not candidate:
        return safe_package_base_name(package_id)
    candidate = candidate.rstrip("_-")
    if not SAFE_BASE_NAME.fullmatch(candidate):
        raise UnsafePackagePath("metadata cannot form a safe base name")
    return candidate


def ordered_metadata(payload: dict[str, Any], key_order: list[str]) -> dict[str, Any]:
    missing = [key for key in key_order if key not in payload]
    extra = [key for key in payload if key not in key_order]
    if missing or extra:
        raise ValueError(f"metadata keys do not match schema: missing={missing}, extra={extra}")
    return {key: payload[key] for key in key_order}


def _ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", normalized)).strip("_")


def ensure_safe_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or value in {"", "."}
    ):
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
