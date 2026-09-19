from media_factory.config import Settings, get_settings
from media_factory.domain.job import JobKind
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.providers.text_to_speech import (
    FakeTextToSpeechProvider,
    TextToSpeechProvider,
)
from media_factory.providers.video_understanding import (
    FakeVideoUnderstandingProvider,
    QwenOpenAICompatibleProvider,
    VideoUnderstandingProvider,
)
from media_factory.services.analysis_service import AnalysisService
from media_factory.services.media_inspector import FFprobeMediaInspector, MediaInspectionError
from media_factory.services.narration_service import NarrationService
from media_factory.services.scene_detection import PySceneDetector
from media_factory.services.video_processing import FFmpegClipExtractor, FFmpegProxyGenerator


def execute_job(job_id: str) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    jobs = SQLAlchemyJobRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    assets = SQLAlchemyAssetRepository(database.session_factory)
    job = jobs.mark_running(job_id)

    try:
        if job.kind is JobKind.INSPECT_ASSET:
            _execute_inspection(settings.ffprobe_bin, packages, assets, job.package_id)
        elif job.kind is JobKind.ANALYZE_VIDEO:
            _execute_analysis(settings, database, packages, assets, job.package_id)
        elif job.kind is JobKind.GENERATE_NARRATION:
            script_id = job.payload.get("script_id")
            if not isinstance(script_id, str) or not script_id:
                raise RuntimeError("generate_narration job requires script_id")
            _execute_narration(settings, database, packages, job.package_id, script_id)
        else:
            raise UnsupportedJobKind(job.kind.value)
        jobs.mark_succeeded(job.id)
    except MediaInspectionError as exc:
        jobs.mark_failed(job.id, code=exc.code, message=str(exc))
        raise
    except Exception as exc:
        jobs.mark_failed(job.id, code="job_failed", message=str(exc))
        raise


class UnsupportedJobKind(RuntimeError):
    pass


class InvalidJobState(RuntimeError):
    def __init__(self, current: str, required: str) -> None:
        super().__init__(f"Job requires package state {required}, current state is {current}")


def _execute_inspection(
    ffprobe_bin: str,
    packages: SQLAlchemyPackageRepository,
    assets: SQLAlchemyAssetRepository,
    package_id: str,
) -> None:
    package = packages.get(package_id)
    if package.state is not PackageState.INSPECTING:
        raise InvalidJobState(package.state.value, PackageState.INSPECTING.value)
    asset = assets.get(package.source_asset_id)
    inspector = FFprobeMediaInspector(ffprobe_bin=ffprobe_bin)
    inspection = inspector.inspect(asset.stored_path)
    assets.update_inspection(asset.id, inspection)
    target = (
        PackageState.DUPLICATE_REVIEW
        if asset.duplicate_of is not None
        else PackageState.READY_FOR_ANALYSIS
    )
    packages.transition(package.id, target=target, expected_version=package.version)


def _execute_analysis(
    settings: Settings,
    database: Database,
    packages: SQLAlchemyPackageRepository,
    assets: SQLAlchemyAssetRepository,
    package_id: str,
) -> None:
    provider = _build_vlm_provider(settings)
    service = AnalysisService(
        packages=packages,
        assets=assets,
        analyses=SQLAlchemyAnalysisRepository(database.session_factory),
        proxy_generator=FFmpegProxyGenerator(ffmpeg_bin=settings.ffmpeg_bin),
        clip_extractor=FFmpegClipExtractor(ffmpeg_bin=settings.ffmpeg_bin),
        scene_detector=PySceneDetector(),
        provider=provider,
        proxy_dir=settings.proxy_dir,
        clip_dir=settings.clip_dir,
        max_clip_duration=settings.max_clip_duration,
        clip_overlap=settings.clip_overlap,
    )
    service.analyze(package_id)


def _build_vlm_provider(settings: Settings) -> VideoUnderstandingProvider:
    if settings.vlm_provider == "fake":
        if not settings.allow_fake_vlm:
            raise RuntimeError("Fake VLM provider is disabled")
        if settings.environment != "development":
            raise RuntimeError("Fake VLM provider is forbidden outside development")
        return FakeVideoUnderstandingProvider()
    if settings.vlm_provider == "qwen":
        return QwenOpenAICompatibleProvider(
            base_url=settings.qwen_base_url,
            api_key=settings.qwen_api_key.get_secret_value(),
            model=settings.qwen_model,
            model_revision=settings.qwen_model_revision,
            timeout_seconds=settings.qwen_timeout_seconds,
            max_retries=settings.qwen_max_retries,
            temperature=settings.qwen_temperature,
            max_tokens=settings.qwen_max_tokens,
        )
    raise RuntimeError(f"Unsupported VLM provider: {settings.vlm_provider}")


def _execute_narration(
    settings: Settings,
    database: Database,
    packages: SQLAlchemyPackageRepository,
    package_id: str,
    script_id: str,
) -> None:
    service = NarrationService(
        packages=packages,
        metadata=SQLAlchemyMetadataRepository(database.session_factory),
        narration=SQLAlchemyNarrationRepository(database.session_factory),
        narration_dir=settings.narration_dir,
        allow_additional_audio=settings.allow_additional_audio,
    )
    service.generate_audio(package_id, script_id, _build_tts_provider(settings))


def _build_tts_provider(settings: Settings) -> TextToSpeechProvider:
    if settings.tts_provider == "fake":
        if not settings.allow_fake_tts:
            raise RuntimeError("Fake TTS provider is disabled")
        if settings.environment != "development":
            raise RuntimeError("Fake TTS provider is forbidden outside development")
        return FakeTextToSpeechProvider()
    raise RuntimeError(f"Unsupported TTS provider: {settings.tts_provider}")
