import os
from pathlib import Path
from typing import BinaryIO, Optional
from uuid import uuid4

from media_factory.domain.models import StoredAsset
from media_factory.services.checksum import UploadTooLarge, copy_and_sha256
from media_factory.services.media_inspector import FFprobeMediaInspector


class InMemoryAssetIndex:
    def __init__(self) -> None:
        self._assets: dict[str, StoredAsset] = {}
        self._by_sha256: dict[str, str] = {}

    def find_by_sha256(self, sha256: str) -> Optional[StoredAsset]:
        asset_id = self._by_sha256.get(sha256)
        return self._assets.get(asset_id) if asset_id else None

    def save(self, asset: StoredAsset) -> None:
        self._assets[asset.id] = asset
        self._by_sha256.setdefault(asset.sha256, asset.id)


class AssetIngestService:
    def __init__(
        self,
        *,
        upload_dir: Path,
        max_upload_bytes: int,
        inspector: FFprobeMediaInspector,
        index: InMemoryAssetIndex,
    ) -> None:
        self.upload_dir = upload_dir
        self.max_upload_bytes = max_upload_bytes
        self.inspector = inspector
        self.index = index

    def ingest(self, source: BinaryIO, original_name: str) -> StoredAsset:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        asset_id = str(uuid4())
        suffix = Path(original_name).suffix.lower()
        destination = self.upload_dir / f"{asset_id}{suffix}"

        try:
            with destination.open("xb") as target:
                size_bytes, sha256 = copy_and_sha256(
                    source,
                    target,
                    max_bytes=self.max_upload_bytes,
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        duplicate = self.index.find_by_sha256(sha256)
        inspection = None
        if duplicate is None:
            try:
                inspection = self.inspector.inspect(destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise

        asset = StoredAsset(
            id=asset_id,
            original_name=os.path.basename(original_name),
            stored_path=destination,
            size_bytes=size_bytes,
            sha256=sha256,
            duplicate_of=duplicate.id if duplicate else None,
            inspection=inspection,
        )
        self.index.save(asset)
        return asset


__all__ = ["AssetIngestService", "InMemoryAssetIndex", "UploadTooLarge"]
