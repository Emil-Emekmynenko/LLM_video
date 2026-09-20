import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from media_factory.providers.object_storage import (
    ClearCheckpoint,
    RemoteObject,
    RemoteObjectAlreadyExists,
    RemoteObjectInvalid,
    RemoteObjectMissing,
    SaveCheckpoint,
    TransferCheckpoint,
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
        self.transport = client._http
        self.bucket_name = bucket
        self.bucket = client.bucket(bucket)
        self.part_size = part_size
        self.timeout_seconds = timeout_seconds
        connection = getattr(client, "_connection", None)
        self.api_base_url = getattr(connection, "API_BASE_URL", "https://storage.googleapis.com")
        self.destination = f"gs://{bucket}"

    def upload_if_absent(
        self,
        source: Path,
        key: str,
        *,
        expected_size: int,
        expected_sha256: str,
        checkpoint: TransferCheckpoint | None = None,
        save_checkpoint: SaveCheckpoint | None = None,
        clear_checkpoint: ClearCheckpoint | None = None,
    ) -> RemoteObject:
        ensure_safe_relative_path(key)
        self._verify_source(source, expected_size, expected_sha256)
        if checkpoint is None:
            session_uri = self._start_session(key, expected_size, expected_sha256)
            offset = 0
            self._save_checkpoint(save_checkpoint, session_uri, offset)
        else:
            if checkpoint.completed_parts or not 0 <= checkpoint.next_offset <= expected_size:
                raise RemoteObjectInvalid(f"GCS checkpoint is invalid: {key}")
            session_uri = checkpoint.session_token
            status, offset = self._query_session(session_uri, expected_size)
            if status == "expired":
                if clear_checkpoint is not None:
                    clear_checkpoint()
                return self.upload_if_absent(
                    source,
                    key,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                    save_checkpoint=save_checkpoint,
                    clear_checkpoint=clear_checkpoint,
                )
            if status == "complete":
                if clear_checkpoint is not None:
                    clear_checkpoint()
                return self._verified_remote(key, expected_size, expected_sha256)
            self._save_checkpoint(save_checkpoint, session_uri, offset)

        with source.open("rb") as source_file:
            source_file.seek(offset)
            while offset < expected_size:
                chunk = source_file.read(min(self.part_size, expected_size - offset))
                if not chunk:
                    raise RemoteObjectInvalid(f"GCS source ended before expected size: {key}")
                end = offset + len(chunk) - 1
                response = self.transport.request(
                    method="PUT",
                    url=session_uri,
                    data=chunk,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{expected_size}",
                    },
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 308:
                    offset = self._confirmed_offset(response, expected_size)
                    self._save_checkpoint(save_checkpoint, session_uri, offset)
                    source_file.seek(offset)
                    continue
                if response.status_code in {200, 201}:
                    offset = expected_size
                    break
                if response.status_code == 412:
                    raise RemoteObjectAlreadyExists(f"remote key already exists: {key}")
                response.raise_for_status()
        if offset != expected_size:
            raise RemoteObjectInvalid(f"GCS upload offset mismatch: {key}")
        if clear_checkpoint is not None:
            clear_checkpoint()
        return self._verified_remote(key, expected_size, expected_sha256)

    def inspect(self, key: str) -> RemoteObject:
        ensure_safe_relative_path(key)
        blob = self.bucket.blob(key, chunk_size=self.part_size)
        try:
            return self._inspect_blob(blob, key, reload=True)
        except Exception as exc:
            if _error_status(exc) == 404:
                raise RemoteObjectMissing(f"remote key is missing: {key}") from exc
            raise

    def _start_session(self, key: str, expected_size: int, expected_sha256: str) -> str:
        query = urlencode({"uploadType": "resumable", "name": key, "ifGenerationMatch": "0"})
        bucket = quote(self.bucket_name, safe="")
        url = f"{self.api_base_url}/upload/storage/v1/b/{bucket}/o?{query}"
        response = self.transport.request(
            method="POST",
            url=url,
            json={"name": key, "metadata": {"sha256": expected_sha256}},
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "application/octet-stream",
                "X-Upload-Content-Length": str(expected_size),
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code == 412:
            raise RemoteObjectAlreadyExists(f"remote key already exists: {key}")
        response.raise_for_status()
        session_uri = response.headers.get("Location")
        if not isinstance(session_uri, str) or not session_uri:
            raise RemoteObjectInvalid("GCS did not return a resumable session URI")
        return session_uri

    def _query_session(self, session_uri: str, expected_size: int) -> tuple[str, int]:
        response = self.transport.request(
            method="PUT",
            url=session_uri,
            data=b"",
            headers={"Content-Length": "0", "Content-Range": f"bytes */{expected_size}"},
            timeout=self.timeout_seconds,
        )
        if response.status_code in {200, 201}:
            return "complete", expected_size
        if response.status_code == 308:
            return "active", self._confirmed_offset(response, expected_size)
        if response.status_code in {404, 410}:
            return "expired", 0
        response.raise_for_status()
        raise RemoteObjectInvalid("unexpected GCS resumable upload status")

    @staticmethod
    def _confirmed_offset(response: Any, expected_size: int) -> int:
        value = response.headers.get("Range")
        if value is None:
            return 0
        try:
            prefix, end = value.split("-", maxsplit=1)
            if prefix != "bytes=0":
                raise ValueError
            offset = int(end) + 1
        except (TypeError, ValueError) as exc:
            raise RemoteObjectInvalid("GCS returned an invalid confirmed range") from exc
        if not 0 <= offset <= expected_size:
            raise RemoteObjectInvalid("GCS confirmed range exceeds source size")
        return offset

    @staticmethod
    def _save_checkpoint(
        callback: SaveCheckpoint | None, session_uri: str, next_offset: int
    ) -> None:
        if callback is not None:
            callback(TransferCheckpoint(session_uri, next_offset, []))

    def _verified_remote(self, key: str, expected_size: int, expected_sha256: str) -> RemoteObject:
        remote = self.inspect(key)
        if remote.size_bytes != expected_size or remote.sha256 != expected_sha256:
            raise RemoteObjectInvalid(f"GCS object mismatch after upload: {key}")
        return remote

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
