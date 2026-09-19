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
    ffmpeg_bin: str = "ffmpeg"
    database_url: str = "sqlite:///./data/media_factory.sqlite3"
    auto_create_schema: bool = True
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "media-tasks"
    proxy_dir: Path = Path("data/proxies")
    clip_dir: Path = Path("data/clips")
    max_clip_duration: float = 15.0
    clip_overlap: float = 2.0
    vlm_provider: str = "fake"
    allow_fake_vlm: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
