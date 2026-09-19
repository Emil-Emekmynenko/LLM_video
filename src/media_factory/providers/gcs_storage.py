import hashlib
from pathlib import Path
from typing import Any

from media_factory.providers.object_storage import (
    RemoteObject,
    RemoteObjectAlreadyExists,
    RemoteObjectInvalid,
    RemoteObjectMissing,
)
from media_factory.services.package_artifacts import ensure_safe_relative_path

GCS_CHUNK_ALIGNMENT = 256 * 1024


class GCSObjectStorageProvider:
    name = "gcs"

    def __init__(
        self,
        client: Any,
        *,
        bucket: str,
        part_size: int,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not bucket:
            raise ValueError("GCS bucket is required")
        if part_size < GCS_CHUNK_ALIGNMENT or part_size % GCS_CHUNK_ALIGNMENT != 0:
            raise ValueError("GCS part size must be a multiple of 256 KiB")
        self.client = client
        self.bucket_name = bucket
        self.bucket = client.bucket(bucket)
        self.part_size = part_size
        self.timeout_seconds = timeout_seconds
        self.destination = f"gs://{bucket}"

    def upload_if_absent(
        self,
        source: Path,
        key: str,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> RemoteObject:
        ensure_safe_relative_path(key)
        self._verify_source(source, expected_size, expected_sha256)
        blob = self.bucket.blob(key, chunk_size=self.part_size)
        blob.metadata = {"sha256": expected_sha256}
        try:
            blob.upload_from_filename(
                str(source),
                if_generation_match=0,
                checksum="auto",
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if _error_status(exc) == 412:
                raise RemoteObjectAlreadyExists(
                    f"remote key already exists: {key}"
                ) from exc
            raise
        remote = self._inspect_blob(blob, key, reload=True)
        if remote.size_bytes != expected_size or remote.sha256 != expected_sha256:
            raise RemoteObjectInvalid(f"GCS object mismatch after upload: {key}")
        return remote

    def inspect(self, key: str) -> RemoteObject:
        ensure_safe_relative_path(key)
        blob = self.bucket.blob(key, chunk_size=self.part_size)
        try:
            return self._inspect_blob(blob, key, reload=True)
        except Exception as exc:
            if _error_status(exc) == 404:
                raise RemoteObjectMissing(f"remote key is missing: {key}") from exc
            raise

    def _inspect_blob(self, blob: Any, key: str, *, reload: bool) -> RemoteObject:
        if reload:
            blob.reload(timeout=self.timeout_seconds)
        metadata = blob.metadata or {}
        sha256 = metadata.get("sha256")
        provider_checksum = blob.crc32c or blob.md5_hash
        if not isinstance(sha256, str) or not isinstance(provider_checksum, str):
            raise RemoteObjectInvalid(f"GCS object lacks required checksums: {key}")
        return RemoteObject(
            key=key,
            size_bytes=int(blob.size),
            sha256=sha256,
            provider_checksum=provider_checksum,
        )

    @staticmethod
    def _verify_source(source: Path, expected_size: int, expected_sha256: str) -> None:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"delivery source is missing or unsafe: {source.name}")
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as source_file:
            while chunk := source_file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise ValueError(f"delivery source changed: {source.name}")


def _error_status(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return int(status_code) if isinstance(status_code, int) else None
