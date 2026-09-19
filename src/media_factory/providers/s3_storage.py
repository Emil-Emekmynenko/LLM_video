import base64
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
    ) -> RemoteObject:
        ensure_safe_relative_path(key)
        self._verify_source(source, expected_size)
        if expected_size <= self.part_size:
            return self._put_object(source, key, expected_size, expected_sha256)
        return self._multipart_upload(source, key, expected_size, expected_sha256)

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
    ) -> RemoteObject:
        create_arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "ChecksumAlgorithm": "SHA256",
            "Metadata": {"sha256": expected_sha256},
        }
        if self.expected_bucket_owner:
            create_arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
        response = self.client.create_multipart_upload(**create_arguments)
        upload_id = str(response["UploadId"])
        parts: list[dict[str, Any]] = []
        part_digests: list[bytes] = []
        full_digest = hashlib.sha256()
        uploaded_size = 0
        try:
            with source.open("rb") as source_file:
                part_number = 1
                while chunk := source_file.read(self.part_size):
                    if part_number > MAX_MULTIPART_PARTS:
                        raise ValueError("S3 multipart upload exceeds 10,000 parts")
                    digest = hashlib.sha256(chunk).digest()
                    full_digest.update(chunk)
                    uploaded_size += len(chunk)
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
                    returned_checksum = uploaded.get("ChecksumSHA256")
                    if returned_checksum != checksum:
                        raise RemoteObjectInvalid(
                            f"S3 part checksum mismatch: {key} part {part_number}"
                        )
                    parts.append(
                        {
                            "ETag": uploaded["ETag"],
                            "PartNumber": part_number,
                            "ChecksumSHA256": checksum,
                        }
                    )
                    part_digests.append(digest)
                    part_number += 1
            if uploaded_size != expected_size or full_digest.hexdigest() != expected_sha256:
                raise ValueError(f"delivery source changed: {source.name}")
            composite = _composite_sha256(part_digests)
            complete_arguments: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": key,
                "UploadId": upload_id,
                "MultipartUpload": {"Parts": parts},
                "IfNoneMatch": "*",
            }
            if self.expected_bucket_owner:
                complete_arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
            completed = self.client.complete_multipart_upload(**complete_arguments)
            if completed.get("ChecksumSHA256") != composite:
                raise RemoteObjectInvalid(f"S3 multipart checksum mismatch: {key}")
        except Exception as exc:
            try:
                abort_arguments: dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Key": key,
                    "UploadId": upload_id,
                }
                if self.expected_bucket_owner:
                    abort_arguments["ExpectedBucketOwner"] = self.expected_bucket_owner
                self.client.abort_multipart_upload(
                    **abort_arguments,
                )
            finally:
                self._raise_upload_error(exc, key)
        remote = self.inspect(key)
        if remote.size_bytes != expected_size or remote.provider_checksum != composite:
            raise RemoteObjectInvalid(f"S3 object mismatch after multipart upload: {key}")
        return remote

    @staticmethod
    def _verify_source(source: Path, expected_size: int) -> None:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"delivery source is missing or unsafe: {source.name}")
        if source.stat().st_size != expected_size:
            raise ValueError(f"delivery source changed: {source.name}")

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
