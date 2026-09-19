from pathlib import Path

import pytest

from media_factory.persistence.database import Database


def test_session_rolls_back_on_error(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'database.sqlite3'}")
    database.create_schema()

    with pytest.raises(RuntimeError):
        with database.session():
            raise RuntimeError("boom")


def test_database_readiness(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'database.sqlite3'}")

    assert database.is_available() is True
