from io import BytesIO

import pytest

from media_factory.services.checksum import UploadTooLarge, copy_and_sha256


def test_copy_and_sha256_streams_content() -> None:
    source = BytesIO(b"video-bytes")
    destination = BytesIO()

    size, digest = copy_and_sha256(source, destination, max_bytes=1024, chunk_size=3)

    assert size == 11
    assert destination.getvalue() == b"video-bytes"
    assert digest == "79fd615a866fe7f9eb4da8d9c41ab57e3bd48056df42fd2c13e4d461a87afbe3"


def test_copy_and_sha256_rejects_oversized_upload() -> None:
    with pytest.raises(UploadTooLarge):
        copy_and_sha256(BytesIO(b"too large"), BytesIO(), max_bytes=3, chunk_size=2)
