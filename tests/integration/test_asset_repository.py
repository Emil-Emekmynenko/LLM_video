from pathlib import Path

from media_factory.domain.models import MediaInspection, StoredAsset, StreamInfo
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database


def test_asset_survives_repository_recreation(tmp_path: Path) -> None:
    database_path = tmp_path / "factory.sqlite3"
    database = Database(f"sqlite:///{database_path}")
    database.create_schema()
    repository = SQLAlchemyAssetRepository(database.session_factory)
    asset = StoredAsset(
        id="asset-1",
        original_name="video.mp4",
        stored_path=tmp_path / "asset-1.mp4",
        size_bytes=123,
        sha256="a" * 64,
        inspection=MediaInspection(
            duration=5.5,
            size_bytes=123,
            format_name="mov,mp4",
            streams=[
                StreamInfo(
                    index=0,
                    codec_type="video",
                    codec_name="h264",
                    width=1920,
                    height=1080,
                )
            ],
        ),
    )

    repository.save(asset)
    recreated_repository = SQLAlchemyAssetRepository(database.session_factory)
    restored = recreated_repository.find_by_sha256("a" * 64)

    assert restored is not None
    assert restored.id == "asset-1"
    assert restored.inspection is not None
    assert restored.inspection.duration == 5.5
    assert restored.inspection.video_streams[0].width == 1920
