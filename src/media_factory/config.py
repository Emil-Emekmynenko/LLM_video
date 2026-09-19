from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
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
    qwen_base_url: str = "http://localhost:8001/v1"
    qwen_api_key: SecretStr = SecretStr("EMPTY")
    qwen_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    qwen_model_revision: str = "e0a319f4d147b3916275a053b0583ca82f351e90"
    qwen_timeout_seconds: float = Field(default=600.0, gt=0)
    qwen_max_retries: int = Field(default=2, ge=0, le=5)
    qwen_temperature: float = Field(default=0.1, ge=0, le=2)
    qwen_max_tokens: int = Field(default=2048, gt=0)
    narration_dir: Path = Path("data/narration")
    tts_provider: str = "fake"
    allow_fake_tts: bool = True
    allow_additional_audio: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
