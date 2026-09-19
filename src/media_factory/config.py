from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MEDIA_FACTORY_",
        extra="ignore",
    )

    environment: str = Field(default="development", validation_alias="MEDIA_FACTORY_ENV")
    upload_dir: Path = Path("data/uploads")
    max_upload_bytes: int = 6 * 1024 * 1024 * 1024
    ffprobe_bin: str = "ffprobe"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

