from typing import Any

from media_factory.config import Settings
from media_factory.providers.gcs_storage import GCSObjectStorageProvider
from media_factory.providers.object_storage import (
    FilesystemObjectStorageProvider,
    ObjectStorageProvider,
)
from media_factory.providers.s3_storage import S3ObjectStorageProvider
from media_factory.services.checkpoint_crypto import CheckpointCipher


def build_checkpoint_cipher(settings: Settings) -> CheckpointCipher | None:
    if settings.delivery_provider == "filesystem":
        return None
    secret = settings.delivery_checkpoint_secret.get_secret_value()
    if not secret:
        raise RuntimeError(
            "cloud delivery requires MEDIA_FACTORY_DELIVERY_CHECKPOINT_SECRET"
        )
    return CheckpointCipher(secret)


def build_object_storage_provider(
    settings: Settings,
    *,
    s3_client: Any | None = None,
    gcs_client: Any | None = None,
) -> ObjectStorageProvider:
    if settings.delivery_provider == "filesystem":
        return FilesystemObjectStorageProvider(
            settings.delivery_filesystem_root,
            chunk_size=settings.delivery_part_size,
        )
    if settings.delivery_provider == "s3":
        if s3_client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise RuntimeError(
                    "S3 delivery requires the optional 'delivery-s3' dependencies"
                ) from exc
            s3_client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url or None,
                region_name=settings.s3_region or None,
                config=Config(
                    connect_timeout=settings.delivery_timeout_seconds,
                    read_timeout=settings.delivery_timeout_seconds,
                ),
            )
        return S3ObjectStorageProvider(
            s3_client,
            bucket=settings.s3_bucket,
            part_size=settings.delivery_part_size,
            expected_bucket_owner=settings.s3_expected_bucket_owner or None,
        )
    if settings.delivery_provider == "gcs":
        if gcs_client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise RuntimeError(
                    "GCS delivery requires the optional 'delivery-gcs' dependencies"
                ) from exc
            gcs_client = storage.Client(project=settings.gcs_project or None)
        return GCSObjectStorageProvider(
            gcs_client,
            bucket=settings.gcs_bucket,
            part_size=settings.delivery_part_size,
            timeout_seconds=settings.delivery_timeout_seconds,
        )
    raise RuntimeError(f"Unsupported delivery provider: {settings.delivery_provider}")
