import hashlib
from pathlib import Path
from typing import BinaryIO

DEFAULT_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_and_sha256(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    max_bytes: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0

    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(max_bytes=max_bytes)
        digest.update(chunk)
        destination.write(chunk)

    return total, digest.hexdigest()


class UploadTooLarge(ValueError):
    def __init__(self, *, max_bytes: int) -> None:
        super().__init__(f"Upload exceeds the configured limit of {max_bytes} bytes")
        self.max_bytes = max_bytes
