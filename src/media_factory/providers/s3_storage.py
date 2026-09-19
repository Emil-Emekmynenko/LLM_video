import base64
import hashlib
from pathlib import Path
from typing import Any

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

MIN_MULTIPART_PART_SIZE = 5 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000


class S3ObjectStorageProvider:
    name = "s3"

    def __init__(
        self,
        client: Any,
        *,
        bucket: str,
        part_size: int,
        expected_bucket_owner: str | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3 bucket is required")
        if part_size < MIN_MULTIPART_PART_SIZE:
            raise ValueError("S3 multipart part size must be at least 5 MiB")
        self.client = client
        self.bucket = bucket
        self.part_size = part_size
        self.expected_bucket_owner = expected_bucket_owner
        self.destination = f"s3://{bucket}"

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
        if expected_size <= self.part_size:
            return self._put_object(source, key, expected_size, expected_sha256)
        return self._multipart_upload(
            source,
            key,
            expected_size,
            expected_sha256,
            checkpoint=checkpoint,
            save_checkpoint=save_checkpoint,
            clear_checkpoint=clear_checkpoint,
        )

    def inspect(self, key: str) -> RemoteObject:
        ensure_safe_relative_path(key)
        arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "ChecksumMode": "ENABLED",
        }
        if self.expected_bucket_owner:
            arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
        try:
            response = self.client.head_object(**arguments)
        except Exception as exc:
            status = _error_status(exc)
            if status == 404:
                raise RemoteObjectMissing(f"remote key is missing: {key}") from exc
            raise
        metadata = response.get("Metadata") or {}
        sha256 = metadata.get("sha256")
        checksum = response.get("ChecksumSHA256")
        if not isinstance(sha256, str) or not isinstance(checksum, str):
            raise RemoteObjectInvalid(f"S3 object lacks required checksums: {key}")
        return RemoteObject(
            key=key,
            size_bytes=int(response["ContentLength"]),
            sha256=sha256,
            provider_checksum=checksum,
        )

    def _put_object(
        self,
        source: Path,
        key: str,
        expected_size: int,
        expected_sha256: str,
    ) -> RemoteObject:
        checksum = _base64_hex_digest(expected_sha256)
        arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "ContentLength": expected_size,
            "ChecksumSHA256": checksum,
            "Metadata": {"sha256": expected_sha256},
            "IfNoneMatch": "*",
        }
        if self.expected_bucket_owner:
            arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
        try:
            with source.open("rb") as source_file:
                self.client.put_object(Body=source_file, **arguments)
        except Exception as exc:
            self._raise_upload_error(exc, key)
        remote = self.inspect(key)
        if remote.provider_checksum != checksum:
            raise RemoteObjectInvalid(f"S3 checksum mismatch after upload: {key}")
        return remote

    def _multipart_upload(
        self,
        source: Path,
        key: str,
        expected_size: int,
        expected_sha256: str,
        *,
        checkpoint: TransferCheckpoint | None,
        save_checkpoint: SaveCheckpoint | None,
        clear_checkpoint: ClearCheckpoint | None,
    ) -> RemoteObject:
        create_arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "ChecksumAlgorithm": "SHA256",
            "Metadata": {"sha256": expected_sha256},
        }
        if self.expected_bucket_owner:
            create_arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
        if checkpoint is None:
            response = self.client.create_multipart_upload(**create_arguments)
            upload_id = str(response["UploadId"])
            parts: list[dict[str, Any]] = []
            uploaded_size = 0
            self._save_checkpoint(save_checkpoint, upload_id, uploaded_size, parts)
        else:
            upload_id = checkpoint.session_token
            parts = [dict(item) for item in checkpoint.completed_parts]
            uploaded_size = checkpoint.next_offset
            self._validate_checkpoint(parts, uploaded_size, expected_size)
            try:
                remote_parts = self._list_remote_parts(key, upload_id)
            except Exception as exc:
                if _error_status(exc) == 404:
                    if clear_checkpoint is not None:
                        clear_checkpoint()
                    return self._multipart_upload(
                        source,
                        key,
                        expected_size,
                        expected_sha256,
                        checkpoint=None,
                        save_checkpoint=save_checkpoint,
                        clear_checkpoint=clear_checkpoint,
                    )
                raise
            parts = self._reconcile_remote_parts(
                source, key, saved=parts, remote=remote_parts, expected_size=expected_size
            )
            uploaded_size = sum(int(part["SizeBytes"]) for part in parts)
            self._save_checkpoint(save_checkpoint, upload_id, uploaded_size, parts)

        part_digests = [base64.b64decode(str(item["ChecksumSHA256"])) for item in parts]
        with source.open("rb") as source_file:
            source_file.seek(uploaded_size)
            part_number = len(parts) + 1
            while chunk := source_file.read(self.part_size):
                if part_number > MAX_MULTIPART_PARTS:
                    self._abort(key, upload_id)
                    if clear_checkpoint is not None:
                        clear_checkpoint()
                    raise ValueError("S3 multipart upload exceeds 10,000 parts")
                digest = hashlib.sha256(chunk).digest()
                checksum = base64.b64encode(digest).decode("ascii")
                upload_arguments: dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                    "Body": chunk,
                    "ContentLength": len(chunk),
                    "ChecksumSHA256": checksum,
                }
                if self.expected_bucket_owner:
                    upload_arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
                uploaded = self.client.upload_part(**upload_arguments)
                if uploaded.get("ChecksumSHA256") != checksum:
                    self._abort(key, upload_id)
                    if clear_checkpoint is not None:
                        clear_checkpoint()
                    raise RemoteObjectInvalid(
                        f"S3 part checksum mismatch: {key} part {part_number}"
                    )
                uploaded_size += len(chunk)
                parts.append(
                    {
                        "ETag": uploaded["ETag"],
                        "PartNumber": part_number,
                        "ChecksumSHA256": checksum,
                        "SizeBytes": len(chunk),
                    }
                )
                part_digests.append(digest)
                self._save_checkpoint(save_checkpoint, upload_id, uploaded_size, parts)
                part_number += 1
        if uploaded_size != expected_size:
            raise RemoteObjectInvalid(f"S3 upload offset mismatch: {key}")
        composite = _composite_sha256(part_digests)
        completion_parts = [
            {name: part[name] for name in ("ETag", "PartNumber", "ChecksumSHA256")}
            for part in parts
        ]
        complete_arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "UploadId": upload_id,
            "MultipartUpload": {"Parts": completion_parts},
            "IfNoneMatch": "*",
        }
        if self.expected_bucket_owner:
            complete_arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
        try:
            completed = self.client.complete_multipart_upload(**complete_arguments)
        except Exception as exc:
            if _error_status(exc) in {409, 412}:
                self._abort(key, upload_id)
                if clear_checkpoint is not None:
                    clear_checkpoint()
            self._raise_upload_error(exc, key)
        if completed.get("ChecksumSHA256") != composite:
            raise RemoteObjectInvalid(f"S3 multipart checksum mismatch: {key}")
        if clear_checkpoint is not None:
            clear_checkpoint()
        remote = self.inspect(key)
        if remote.size_bytes != expected_size or remote.provider_checksum != composite:
            raise RemoteObjectInvalid(f"S3 object mismatch after multipart upload: {key}")
        return remote

    @staticmethod
    def _verify_source(source: Path, expected_size: int, expected_sha256: str) -> None:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"delivery source is missing or unsafe: {source.name}")
        digest = hashlib.sha256()
        with source.open("rb") as source_file:
            while chunk := source_file.read(1024 * 1024):
                digest.update(chunk)
        if source.stat().st_size != expected_size or digest.hexdigest() != expected_sha256:
            raise ValueError(f"delivery source changed: {source.name}")

    def _list_remote_parts(self, key: str, upload_id: str) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "UploadId": upload_id,
        }
        if self.expected_bucket_owner:
            arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
        actual: list[dict[str, Any]] = []
        while True:
            response = self.client.list_parts(**arguments)
            for part in response.get("Parts") or []:
                actual.append(
                    {
                        "PartNumber": part["PartNumber"],
                        "ETag": part["ETag"],
                        "ChecksumSHA256": part["ChecksumSHA256"],
                        "SizeBytes": part["Size"],
                    }
                )
            if not response.get("IsTruncated"):
                break
            arguments["PartNumberMarker"] = response["NextPartNumberMarker"]
        return actual

    def _reconcile_remote_parts(
        self,
        source: Path,
        key: str,
        *,
        saved: list[dict[str, Any]],
        remote: list[dict[str, Any]],
        expected_size: int,
    ) -> list[dict[str, Any]]:
        if len(remote) < len(saved):
            raise RemoteObjectInvalid(f"S3 remote upload lost checkpointed parts: {key}")
        for checkpoint_part, remote_part in zip(saved, remote, strict=False):
            if any(
                checkpoint_part.get(name) != remote_part.get(name)
                for name in ("PartNumber", "ETag", "ChecksumSHA256", "SizeBytes")
            ):
                raise RemoteObjectInvalid(f"S3 checkpoint differs from remote parts: {key}")
        self._validate_checkpoint(
            remote,
            sum(int(part["SizeBytes"]) for part in remote),
            expected_size,
        )
        with source.open("rb") as source_file:
            for part in remote:
                size = int(part["SizeBytes"])
                chunk = source_file.read(size)
                checksum = base64.b64encode(hashlib.sha256(chunk).digest()).decode("ascii")
                if len(chunk) != size or checksum != part["ChecksumSHA256"]:
                    raise RemoteObjectInvalid(f"S3 remote part differs from source: {key}")
        return remote

    @staticmethod
    def _validate_checkpoint(
        parts: list[dict[str, Any]], next_offset: int, expected_size: int
    ) -> None:
        if [part.get("PartNumber") for part in parts] != list(range(1, len(parts) + 1)):
            raise RemoteObjectInvalid("S3 checkpoint part sequence is invalid")
        if sum(int(part.get("SizeBytes", -1)) for part in parts) != next_offset:
            raise RemoteObjectInvalid("S3 checkpoint offset is invalid")
        if not 0 <= next_offset <= expected_size:
            raise RemoteObjectInvalid("S3 checkpoint exceeds source size")

    @staticmethod
    def _save_checkpoint(
        callback: SaveCheckpoint | None,
        upload_id: str,
        next_offset: int,
        parts: list[dict[str, Any]],
    ) -> None:
        if callback is not None:
            callback(TransferCheckpoint(upload_id, next_offset, [dict(item) for item in parts]))

    def _abort(self, key: str, upload_id: str) -> None:
        arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "UploadId": upload_id,
        }
        if self.expected_bucket_owner:
            arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
        self.client.abort_multipart_upload(**arguments)

    @staticmethod
    def _raise_upload_error(exc: Exception, key: str) -> None:
        if _error_status(exc) in {409, 412}:
            raise RemoteObjectAlreadyExists(f"remote key already exists: {key}") from exc
        raise exc


def _base64_hex_digest(value: str) -> str:
    return base64.b64encode(bytes.fromhex(value)).decode("ascii")


def _composite_sha256(part_digests: list[bytes]) -> str:
    digest = hashlib.sha256(b"".join(part_digests)).digest()
    return f"{base64.b64encode(digest).decode('ascii')}-{len(part_digests)}"


def _error_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("HTTPStatusCode"), int):
        return int(metadata["HTTPStatusCode"])
    return None
