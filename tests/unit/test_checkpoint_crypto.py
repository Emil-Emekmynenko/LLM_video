from pathlib import Path

import pytest
from sqlalchemy import select

from media_factory.persistence.database import Database
from media_factory.persistence.delivery_repository import SQLAlchemyDeliveryRepository
from media_factory.persistence.tables import UploadCheckpointRow
from media_factory.providers.object_storage import TransferCheckpoint
from media_factory.services.checkpoint_crypto import (
    CheckpointCipher,
    CheckpointDecryptionError,
)


def test_checkpoint_session_token_is_encrypted_and_authenticated() -> None:
    cipher = CheckpointCipher("a-long-random-checkpoint-secret-123456")
    token = "https://storage.googleapis.com/resumable/session-sensitive-token"

    encrypted = cipher.encrypt(token)

    assert token not in encrypted
    assert cipher.decrypt(encrypted) == token
    with pytest.raises(CheckpointDecryptionError):
        CheckpointCipher("another-long-random-secret-123456789").decrypt(encrypted)


def test_repository_never_persists_plain_session_token(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'checkpoints.sqlite3'}")
    database.create_schema()
    repository = SQLAlchemyDeliveryRepository(
        database.session_factory,
        checkpoint_cipher=CheckpointCipher("a-long-random-checkpoint-secret-123456"),
    )
    checkpoint = TransferCheckpoint(
        session_token="sensitive-session-token",
        next_offset=1024,
        completed_parts=[{"PartNumber": 1, "SizeBytes": 1024}],
    )

    repository.save_checkpoint(
        delivery_attempt_id="delivery-1",
        remote_key="prefix/video.mp4",
        provider="s3",
        source_size_bytes=2048,
        source_sha256="a" * 64,
        checkpoint=checkpoint,
    )

    with database.session_factory() as session:
        row = session.scalar(select(UploadCheckpointRow))
        assert row is not None
        assert "sensitive-session-token" not in row.encrypted_session_token
    assert repository.get_checkpoint(
        delivery_attempt_id="delivery-1",
        remote_key="prefix/video.mp4",
        provider="s3",
        source_size_bytes=2048,
        source_sha256="a" * 64,
    ) == checkpoint
