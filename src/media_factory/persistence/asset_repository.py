from pathlib import Path

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError
from media_factory.domain.models import MediaInspection, StoredAsset
from media_factory.persistence.tables import AssetRow


class SQLAlchemyAssetRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def find_by_sha256(self, sha256: str) -> StoredAsset | None:
        statement: Select[tuple[AssetRow]] = (
            select(AssetRow)
            .where(AssetRow.sha256 == sha256)
            .where(AssetRow.duplicate_of.is_(None))
            .order_by(AssetRow.created_at.asc())
            .limit(1)
        )
        with self.session_factory() as session:
            row = session.scalar(statement)
            return self._to_domain(row) if row else None

    def get(self, asset_id: str) -> StoredAsset:
        with self.session_factory() as session:
            row = session.get(AssetRow, asset_id)
            if row is None:
                raise EntityNotFoundError("asset", asset_id)
            return self._to_domain(row)

    def save(self, asset: StoredAsset) -> None:
        inspection = asset.inspection.model_dump(mode="json") if asset.inspection else None
        row = AssetRow(
            id=asset.id,
            original_name=asset.original_name,
            stored_path=str(asset.stored_path),
            size_bytes=asset.size_bytes,
            sha256=asset.sha256,
            duplicate_of=asset.duplicate_of,
            inspection=inspection,
        )
        with self.session_factory.begin() as session:
            session.add(row)

    def update_inspection(self, asset_id: str, inspection: MediaInspection) -> StoredAsset:
        with self.session_factory.begin() as session:
            row = session.get(AssetRow, asset_id)
            if row is None:
                raise EntityNotFoundError("asset", asset_id)
            row.inspection = inspection.model_dump(mode="json")
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: AssetRow) -> StoredAsset:
        inspection = MediaInspection.model_validate(row.inspection) if row.inspection else None
        return StoredAsset(
            id=row.id,
            original_name=row.original_name,
            stored_path=Path(row.stored_path),
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            duplicate_of=row.duplicate_of,
            inspection=inspection,
        )
