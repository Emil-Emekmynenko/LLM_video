import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from media_factory.services.package_artifacts import ensure_safe_relative_path


class RemoteObjectAlreadyExists(RuntimeError):
    code = "remote_object_already_exists"


class RemoteObjectMissing(RuntimeError):
    code = "remote_object_missing"


class RemoteObjectInvalid(RuntimeError):
    code = "remote_object_invalid"


@dataclass(frozen=True)
class RemoteObject:
    key: str
    size_bytes: int
    sha256: str
    provider_checksum: str | None = None


@dataclass(frozen=True)
class TransferCheckpoint:
    session_token: str
    next_offset: int
    completed_parts: list[dict[str, Any]]


SaveCheckpoint = Callable[[TransferCheckpoint], None]
ClearCheckpoint = Callable[[], None]


class ObjectStorageProvider(Protocol):
    name: str
    destination: str

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
    ) -> RemoteObject: ...

    def inspect(self, key: str) -> RemoteObject: ...


class FilesystemObjectStorageProvider:
    name = "filesystem"

    def __init__(self, root: Path, *, chunk_size: int) -> None:
        self.root = root
        self.chunk_size = chunk_size
        self.destination = str(root)

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
        destination = self._path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_safe_parent(destination)
        if destination.exists():
            raise RemoteObjectAlreadyExists(f"remote key already exists: {key}")

        temporary_dir = self.root / ".uploads"
        temporary_dir.mkdir(parents=True, exist_ok=True)
        temporary = temporary_dir / f"{uuid4()}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as source_file, temporary.open("xb") as target_file:
                while chunk := source_file.read(self.chunk_size):
                    target_file.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            actual_sha256 = digest.hexdigest()
            if size != expected_size or actual_sha256 != expected_sha256:
                raise ValueError(f"local delivery source changed: {source.name}")
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise RemoteObjectAlreadyExists(f"remote key already exists: {key}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return RemoteObject(
            key=key,
            size_bytes=size,
            sha256=actual_sha256,
            provider_checksum=actual_sha256,
        )

    def inspect(self, key: str) -> RemoteObject:
        path = self._path_for(key)
        if not path.is_file() or path.is_symlink():
            raise RemoteObjectMissing(f"remote key is missing: {key}")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(self.chunk_size):
                digest.update(chunk)
                size += len(chunk)
        checksum = digest.hexdigest()
        return RemoteObject(
            key=key,
            size_bytes=size,
            sha256=checksum,
            provider_checksum=checksum,
        )

    def _path_for(self, key: str) -> Path:
        ensure_safe_relative_path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("delivery root cannot be a symlink")
        path = self.root / key
        if not path.resolve(strict=False).is_relative_to(self.root.resolve()):
            raise ValueError("remote key escapes delivery root")
        return path

    def _ensure_safe_parent(self, destination: Path) -> None:
        root = self.root.resolve()
        if not destination.parent.resolve().is_relative_to(root):
            raise ValueError("remote key parent escapes delivery root")
        current = destination.parent
        while current != self.root:
            if current.is_symlink():
                raise ValueError("delivery path cannot contain symlinks")
            current = current.parent
