import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest

from media_factory.config import Settings
from media_factory.providers.gcs_storage import GCSObjectStorageProvider
from media_factory.providers.object_storage import (
    RemoteObjectAlreadyExists,
    TransferCheckpoint,
)
from media_factory.providers.s3_storage import S3ObjectStorageProvider
from media_factory.providers.storage_factory import build_object_storage_provider
from media_factory.services.checksum import sha256_file


class FakeCloudError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"cloud error {status}")
        self.code = status
        self.response = {
            "status_code": status,
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.uploads: dict[str, dict[str, Any]] = {}
        self.put_if_none_match: str | None = None
        self.complete_if_none_match: str | None = None
        self.fail_part_after_accept_once: int | None = None
        self.uploaded_part_numbers: list[int] = []

    def put_object(self, **kwargs: Any) -> dict[str, str]:
        key = str(kwargs["Key"])
        self.put_if_none_match = str(kwargs["IfNoneMatch"])
        if key in self.objects:
            raise FakeCloudError(412)
        body = kwargs["Body"].read()
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        assert checksum == kwargs["ChecksumSHA256"]
        self.objects[key] = {
            "body": body,
            "metadata": kwargs["Metadata"],
            "checksum": checksum,
        }
        return {"ChecksumSHA256": checksum}

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        upload_id = "upload-1"
        self.uploads[upload_id] = {
            "key": kwargs["Key"],
            "metadata": kwargs["Metadata"],
            "parts": {},
        }
        return {"UploadId": upload_id}

    def upload_part(self, **kwargs: Any) -> dict[str, str]:
        upload = self.uploads[str(kwargs["UploadId"])]
        body = bytes(kwargs["Body"])
        part_number = int(kwargs["PartNumber"])
        self.uploaded_part_numbers.append(part_number)
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        assert checksum == kwargs["ChecksumSHA256"]
        upload["parts"][part_number] = (body, checksum)
        if self.fail_part_after_accept_once == part_number:
            self.fail_part_after_accept_once = None
            raise RuntimeError("interrupted after S3 accepted part")
        return {"ETag": f"etag-{part_number}", "ChecksumSHA256": checksum}

    def list_parts(self, **kwargs: Any) -> dict[str, Any]:
        upload_id = str(kwargs["UploadId"])
        if upload_id not in self.uploads:
            raise FakeCloudError(404)
        parts = self.uploads[upload_id]["parts"]
        return {
            "Parts": [
                {
                    "PartNumber": number,
                    "ETag": f"etag-{number}",
                    "ChecksumSHA256": value[1],
                    "Size": len(value[0]),
                }
                for number, value in sorted(parts.items())
            ],
            "IsTruncated": False,
        }

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        upload = self.uploads[str(kwargs["UploadId"])]
        key = str(kwargs["Key"])
        self.complete_if_none_match = str(kwargs["IfNoneMatch"])
        if key in self.objects:
            raise FakeCloudError(412)
        ordered = [upload["parts"][index] for index in sorted(upload["parts"])]
        body = b"".join(item[0] for item in ordered)
        digests = b"".join(base64.b64decode(item[1]) for item in ordered)
        checksum = (
            base64.b64encode(hashlib.sha256(digests).digest()).decode("ascii") + f"-{len(ordered)}"
        )
        self.objects[key] = {
            "body": body,
            "metadata": upload["metadata"],
            "checksum": checksum,
        }
        return {"ChecksumSHA256": checksum}

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        self.uploads.pop(str(kwargs["UploadId"]), None)

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise FakeCloudError(404)
        item = self.objects[key]
        return {
            "ContentLength": len(item["body"]),
            "ChecksumSHA256": item["checksum"],
            "Metadata": item["metadata"],
        }


class FakeGCSBlob:
    def __init__(self, name: str, chunk_size: int) -> None:
        self.name = name
        self.chunk_size = chunk_size
        self.metadata: dict[str, str] | None = None
        self.size: int | None = None
        self.crc32c: str | None = None
        self.md5_hash: str | None = None
        self.exists = False

    def reload(self, **kwargs: Any) -> None:
        if not self.exists:
            raise FakeCloudError(404)


class FakeGCSBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, FakeGCSBlob] = {}

    def blob(self, name: str, *, chunk_size: int) -> FakeGCSBlob:
        if name not in self.blobs:
            self.blobs[name] = FakeGCSBlob(name, chunk_size)
        return self.blobs[name]


class FakeGCSClient:
    def __init__(self) -> None:
        self.value = FakeGCSBucket()
        self._http = FakeGCSTransport(self)

    def bucket(self, name: str) -> FakeGCSBucket:
        return self.value


class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise FakeCloudError(self.status_code)


class FakeGCSTransport:
    def __init__(self, client: FakeGCSClient) -> None:
        self.client = client
        self.sessions: dict[str, dict[str, Any]] = {}
        self.fail_after_accept_once = False
        self.upload_ranges: list[str] = []
        self.initiation_url: str | None = None

    def request(self, *, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "POST":
            self.initiation_url = url
            metadata = kwargs["json"]
            key = str(metadata["name"])
            blob = self.client.value.blob(key, chunk_size=256 * 1024)
            if blob.exists:
                return FakeResponse(412)
            session_uri = f"https://upload.test/session-{len(self.sessions) + 1}"
            self.sessions[session_uri] = {
                "key": key,
                "metadata": metadata["metadata"],
                "body": bytearray(),
                "total": int(kwargs["headers"]["X-Upload-Content-Length"]),
            }
            return FakeResponse(200, {"Location": session_uri})
        session = self.sessions[url]
        content_range = str(kwargs["headers"]["Content-Range"])
        data = bytes(kwargs.get("data", b""))
        if content_range.startswith("bytes */"):
            length = len(session["body"])
            headers = {} if length == 0 else {"Range": f"bytes=0-{length - 1}"}
            return FakeResponse(308, headers)
        self.upload_ranges.append(content_range)
        start = int(content_range.split(" ", 1)[1].split("-", 1)[0])
        assert start == len(session["body"])
        session["body"].extend(data)
        if self.fail_after_accept_once:
            self.fail_after_accept_once = False
            raise RuntimeError("connection lost after server accepted chunk")
        length = len(session["body"])
        if length < session["total"]:
            return FakeResponse(308, {"Range": f"bytes=0-{length - 1}"})
        blob = self.client.value.blob(str(session["key"]), chunk_size=256 * 1024)
        blob.metadata = session["metadata"]
        blob.size = length
        blob.crc32c = base64.b64encode(hashlib.sha256(session["body"]).digest()[:4]).decode("ascii")
        blob.exists = True
        return FakeResponse(200)


def test_s3_single_upload_uses_checksum_and_conditional_write(tmp_path: Path) -> None:
    source = tmp_path / "small.bin"
    source.write_bytes(b"small-object")
    client = FakeS3Client()
    provider = S3ObjectStorageProvider(
        client,
        bucket="delivery",
        part_size=5 * 1024 * 1024,
    )

    uploaded = provider.upload_if_absent(
        source,
        "prefix/small.bin",
        expected_size=source.stat().st_size,
        expected_sha256=sha256_file(source),
    )

    assert uploaded == provider.inspect(uploaded.key)
    assert client.put_if_none_match == "*"
    with pytest.raises(RemoteObjectAlreadyExists):
        provider.upload_if_absent(
            source,
            uploaded.key,
            expected_size=source.stat().st_size,
            expected_sha256=sha256_file(source),
        )


def test_s3_large_upload_uses_multipart_checksums_and_conditional_complete(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"a" * (5 * 1024 * 1024) + b"tail")
    client = FakeS3Client()
    provider = S3ObjectStorageProvider(
        client,
        bucket="delivery",
        part_size=5 * 1024 * 1024,
    )

    uploaded = provider.upload_if_absent(
        source,
        "prefix/large.bin",
        expected_size=source.stat().st_size,
        expected_sha256=sha256_file(source),
    )

    assert uploaded.size_bytes == source.stat().st_size
    assert uploaded.provider_checksum is not None
    assert uploaded.provider_checksum.endswith("-2")
    assert client.complete_if_none_match == "*"


def test_s3_multipart_resumes_from_confirmed_parts(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"a" * (5 * 1024 * 1024) + b"tail")
    client = FakeS3Client()
    client.fail_part_after_accept_once = 2
    provider = S3ObjectStorageProvider(
        client,
        bucket="delivery",
        part_size=5 * 1024 * 1024,
    )
    saved: TransferCheckpoint | None = None

    def save(value: TransferCheckpoint) -> None:
        nonlocal saved
        saved = value

    with pytest.raises(RuntimeError, match="interrupted after S3 accepted part"):
        provider.upload_if_absent(
            source,
            "prefix/large.bin",
            expected_size=source.stat().st_size,
            expected_sha256=sha256_file(source),
            save_checkpoint=save,
        )

    assert saved is not None
    assert saved.next_offset == 5 * 1024 * 1024
    uploaded = provider.upload_if_absent(
        source,
        "prefix/large.bin",
        expected_size=source.stat().st_size,
        expected_sha256=sha256_file(source),
        checkpoint=saved,
        save_checkpoint=save,
    )

    assert uploaded.size_bytes == source.stat().st_size
    assert client.uploaded_part_numbers.count(1) == 1
    assert client.uploaded_part_numbers.count(2) == 1


def test_gcs_upload_is_resumable_checksum_checked_and_create_only(tmp_path: Path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"gcs-content")
    client = FakeGCSClient()
    provider = GCSObjectStorageProvider(
        client,
        bucket="delivery",
        part_size=32 * 1024 * 1024,
    )

    uploaded = provider.upload_if_absent(
        source,
        "prefix/video.mp4",
        expected_size=source.stat().st_size,
        expected_sha256=sha256_file(source),
    )

    blob = client.value.blobs[uploaded.key]
    assert blob.exists
    assert client._http.initiation_url is not None
    assert "ifGenerationMatch=0" in client._http.initiation_url
    assert provider.inspect(uploaded.key) == uploaded
    with pytest.raises(RemoteObjectAlreadyExists):
        provider.upload_if_absent(
            source,
            uploaded.key,
            expected_size=source.stat().st_size,
            expected_sha256=sha256_file(source),
        )


def test_gcs_resume_queries_server_offset_after_lost_response(tmp_path: Path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"a" * (256 * 1024) + b"tail")
    client = FakeGCSClient()
    client._http.fail_after_accept_once = True
    provider = GCSObjectStorageProvider(
        client,
        bucket="delivery",
        part_size=256 * 1024,
    )
    saved: TransferCheckpoint | None = None

    def save(value: TransferCheckpoint) -> None:
        nonlocal saved
        saved = value

    with pytest.raises(RuntimeError, match="connection lost"):
        provider.upload_if_absent(
            source,
            "prefix/video.mp4",
            expected_size=source.stat().st_size,
            expected_sha256=sha256_file(source),
            save_checkpoint=save,
        )

    assert saved is not None
    assert saved.next_offset == 0
    uploaded = provider.upload_if_absent(
        source,
        "prefix/video.mp4",
        expected_size=source.stat().st_size,
        expected_sha256=sha256_file(source),
        checkpoint=saved,
        save_checkpoint=save,
    )

    assert uploaded.size_bytes == source.stat().st_size
    assert client._http.upload_ranges == [
        f"bytes 0-{256 * 1024 - 1}/{source.stat().st_size}",
        f"bytes {256 * 1024}-{source.stat().st_size - 1}/{source.stat().st_size}",
    ]


def test_storage_factory_selects_cloud_adapters_with_injected_clients() -> None:
    s3 = build_object_storage_provider(
        Settings(
            delivery_provider="s3",
            s3_bucket="s3-delivery",
            delivery_part_size=5 * 1024 * 1024,
        ),
        s3_client=FakeS3Client(),
    )
    gcs = build_object_storage_provider(
        Settings(
            delivery_provider="gcs",
            gcs_bucket="gcs-delivery",
            delivery_part_size=32 * 1024 * 1024,
        ),
        gcs_client=FakeGCSClient(),
    )

    assert isinstance(s3, S3ObjectStorageProvider)
    assert isinstance(gcs, GCSObjectStorageProvider)
