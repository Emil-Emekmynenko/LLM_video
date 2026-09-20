#!/usr/bin/env python3
import argparse
import json
import shutil
from typing import Any

from media_factory.config import get_settings
from media_factory.persistence.database import Database
from media_factory.services.customer_schema_service import load_customer_schema
from media_factory.services.job_queue import RedisJobQueue


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Media Factory pilot prerequisites")
    parser.add_argument(
        "--external",
        action="store_true",
        help="also require production providers, authentication, and delivery safeguards",
    )
    args = parser.parse_args()
    settings = get_settings()
    checks: dict[str, dict[str, Any]] = {}

    def add(name: str, ok: bool, detail: str) -> None:
        checks[name] = {"ok": ok, "detail": detail}

    ffmpeg = shutil.which(settings.ffmpeg_bin)
    ffprobe = shutil.which(settings.ffprobe_bin)
    add("ffmpeg", ffmpeg is not None, ffmpeg or f"not found: {settings.ffmpeg_bin}")
    add("ffprobe", ffprobe is not None, ffprobe or f"not found: {settings.ffprobe_bin}")
    add("database", Database(settings.database_url).is_available(), settings.database_url)
    queue = RedisJobQueue(settings.redis_url, settings.queue_name)
    add("redis", queue.is_available(), settings.redis_url)
    try:
        schema = load_customer_schema(
            settings.default_customer,
            settings.default_customer_schema_version,
            schema_dir=settings.customer_schema_dir,
        )
        add("customer_schema", True, f"{schema.customer}@{schema.version}")
    except Exception as exc:
        add("customer_schema", False, str(exc))

    if settings.tts_provider == "piper":
        piper_ok = (
            shutil.which(settings.piper_bin) is not None
            and settings.piper_model_path is not None
            and settings.piper_model_path.is_file()
        )
        add("tts", piper_ok, f"piper model: {settings.piper_model_path}")
    else:
        add(
            "tts",
            settings.environment == "development" and settings.allow_fake_tts,
            f"provider={settings.tts_provider}",
        )

    if args.external:
        add("auth", settings.auth_enabled, "RBAC must be enabled")
        add(
            "vlm",
            settings.vlm_provider != "fake" and not settings.allow_fake_vlm,
            f"provider={settings.vlm_provider}",
        )
        add(
            "asr",
            settings.asr_provider != "fake" and not settings.allow_fake_asr,
            f"provider={settings.asr_provider}",
        )
        cloud_delivery = settings.delivery_provider in {"s3", "gcs"}
        checkpoint_secret = settings.delivery_checkpoint_secret.get_secret_value()
        add(
            "delivery_checkpoint_encryption",
            not cloud_delivery or len(checkpoint_secret) >= 32,
            f"provider={settings.delivery_provider}",
        )

    ready = all(item["ok"] for item in checks.values())
    report = {
        "ready": ready,
        "mode": "external" if args.external else "development",
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
