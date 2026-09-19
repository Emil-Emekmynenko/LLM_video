from pathlib import Path

import pytest

from media_factory.providers.object_storage import (
    FilesystemObjectStorageProvider,
    RemoteObjectAlreadyExists,
)
from media_factory.services.checksum import sha256_file


def test_filesystem_provider_streams_verifies_and_never_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video-content")
    provider = FilesystemObjectStorageProvider(
        tmp_path / "remote",
        chunk_size=64 * 1024,
    )

    uploaded = provider.upload_if_absent(
        source,
        "delivery/master.mp4",
        expected_size=source.stat().st_size,
        expected_sha256=sha256_file(source),
    )

    assert provider.inspect(uploaded.key) == uploaded
    with pytest.raises(RemoteObjectAlreadyExists):
        provider.upload_if_absent(
            source,
            uploaded.key,
            expected_size=source.stat().st_size,
            expected_sha256=sha256_file(source),
        )
    assert (tmp_path / "remote" / uploaded.key).read_bytes() == b"video-content"


def test_filesystem_provider_rejects_unsafe_key(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video-content")
    provider = FilesystemObjectStorageProvider(
        tmp_path / "remote",
        chunk_size=64 * 1024,
    )

    with pytest.raises(ValueError):
        provider.upload_if_absent(
            source,
            "../outside.mp4",
            expected_size=source.stat().st_size,
            expected_sha256=sha256_file(source),
        )
