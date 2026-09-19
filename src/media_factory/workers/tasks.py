from media_factory.config import Settings, get_settings
from media_factory.domain.job import JobKind
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.delivery_repository import SQLAlchemyDeliveryRepository
from media_factory.persistence.export_repository import SQLAlchemyExportRepository
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.master_repository import SQLAlchemyMasterRepository
from media_factory.persistence.metadata_repository import SQLAlchemyMetadataRepository
from media_factory.persistence.narration_repository import SQLAlchemyNarrationRepository
from media_factory.persistence.package_build_repository import (
    SQLAlchemyPackageBuildRepository,
)
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.qa_repository import SQLAlchemyQARepository
from media_factory.persistence.transcription_repository import (
    SQLAlchemyTranscriptionRepository,
)
from media_factory.providers.speech_recognition import (
    FakeSpeechRecognitionProvider,
    FasterWhisperProvider,
    SpeechRecognitionProvider,
)
from media_factory.providers.storage_factory import (
    build_checkpoint_cipher,
    build_object_storage_provider,
)
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
from media_factory.services.delivery_service import DeliveryService
from media_factory.services.export_service import LocalExportService
from media_factory.services.master_processing import (
    FFmpegDecodeValidator,
    FFmpegMasterAssembler,
)
from media_factory.services.master_service import MasterService
from media_factory.services.media_inspector import FFprobeMediaInspector, MediaInspectionError
from media_factory.services.narration_service import NarrationService
from media_factory.services.packaging_service import PackagingService
from media_factory.services.scene_detection import PySceneDetector
from media_factory.services.transcription_service import TranscriptionService
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
        elif job.kind is JobKind.BUILD_MASTER:
            _execute_master_build(settings, database, packages, assets, job.package_id)
        elif job.kind is JobKind.TRANSCRIBE_MASTER:
            _execute_transcription(settings, database, packages, job.package_id)
        elif job.kind is JobKind.BUILD_PACKAGE:
            _execute_package_build(settings, database, packages, assets, job.package_id)
        elif job.kind is JobKind.EXPORT_PACKAGE:
            package_build_id = job.payload.get("package_build_id")
            if not isinstance(package_build_id, str) or not package_build_id:
                raise RuntimeError("export_package job requires package_build_id")
            _execute_local_export(
                settings,
                database,
                packages,
                job.package_id,
                package_build_id,
            )
        elif job.kind is JobKind.DELIVER_PACKAGE:
            delivery_id = job.payload.get("delivery_id")
            if not isinstance(delivery_id, str) or not delivery_id:
                raise RuntimeError("deliver_package job requires delivery_id")
            _execute_delivery(settings, database, packages, delivery_id)
        else:
            raise UnsupportedJobKind(job.kind.value)
        jobs.mark_succeeded(job.id)
    except MediaInspectionError as exc:
        jobs.mark_failed(job.id, code=exc.code, message=str(exc))
        raise
    except Exception as exc:
        jobs.mark_failed(
            job.id,
            code=str(getattr(exc, "code", "job_failed")),
            message=str(exc),
        )
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


def _execute_master_build(
    settings: Settings,
    database: Database,
    packages: SQLAlchemyPackageRepository,
    assets: SQLAlchemyAssetRepository,
    package_id: str,
) -> None:
    service = MasterService(
        packages=packages,
        assets=assets,
        narration=SQLAlchemyNarrationRepository(database.session_factory),
        masters=SQLAlchemyMasterRepository(database.session_factory),
        assembler=FFmpegMasterAssembler(ffmpeg_bin=settings.ffmpeg_bin),
        decoder=FFmpegDecodeValidator(ffmpeg_bin=settings.ffmpeg_bin),
        inspector=FFprobeMediaInspector(ffprobe_bin=settings.ffprobe_bin),
        master_dir=settings.master_dir,
        duration_tolerance=settings.master_duration_tolerance,
        container_extension=settings.master_container_extension,
    )
    service.build(package_id)


def _execute_transcription(
    settings: Settings,
    database: Database,
    packages: SQLAlchemyPackageRepository,
    package_id: str,
) -> None:
    service = TranscriptionService(
        packages=packages,
        masters=SQLAlchemyMasterRepository(database.session_factory),
        transcriptions=SQLAlchemyTranscriptionRepository(database.session_factory),
        duration_tolerance=settings.transcript_duration_tolerance,
    )
    service.transcribe(package_id, _build_asr_provider(settings))


def _build_asr_provider(settings: Settings) -> SpeechRecognitionProvider:
    if settings.asr_provider == "fake":
        if not settings.allow_fake_asr:
            raise RuntimeError("Fake ASR provider is disabled")
        if settings.environment != "development":
            raise RuntimeError("Fake ASR provider is forbidden outside development")
        return FakeSpeechRecognitionProvider()
    if settings.asr_provider == "faster-whisper":
        return FasterWhisperProvider(
            model_name=settings.faster_whisper_model,
            model_revision=settings.faster_whisper_model_revision,
            device=settings.faster_whisper_device,
            compute_type=settings.faster_whisper_compute_type,
            language=settings.faster_whisper_language,
            beam_size=settings.faster_whisper_beam_size,
            vad_filter=settings.faster_whisper_vad_filter,
        )
    raise RuntimeError(f"Unsupported ASR provider: {settings.asr_provider}")


def _execute_package_build(
    settings: Settings,
    database: Database,
    packages: SQLAlchemyPackageRepository,
    assets: SQLAlchemyAssetRepository,
    package_id: str,
) -> None:
    service = PackagingService(
        packages=packages,
        assets=assets,
        analyses=SQLAlchemyAnalysisRepository(database.session_factory),
        metadata=SQLAlchemyMetadataRepository(database.session_factory),
        narration=SQLAlchemyNarrationRepository(database.session_factory),
        masters=SQLAlchemyMasterRepository(database.session_factory),
        transcriptions=SQLAlchemyTranscriptionRepository(database.session_factory),
        builds=SQLAlchemyPackageBuildRepository(database.session_factory),
        decoder=FFmpegDecodeValidator(ffmpeg_bin=settings.ffmpeg_bin),
        package_dir=settings.package_dir,
        customer=settings.default_customer,
        schema_version=settings.default_customer_schema_version,
        duration_tolerance=settings.transcript_duration_tolerance,
        schema_dir=settings.customer_schema_dir,
    )
    service.build(package_id)


def _execute_local_export(
    settings: Settings,
    database: Database,
    packages: SQLAlchemyPackageRepository,
    package_id: str,
    package_build_id: str,
) -> None:
    service = LocalExportService(
        packages=packages,
        builds=SQLAlchemyPackageBuildRepository(database.session_factory),
        reviews=SQLAlchemyQARepository(database.session_factory),
        exports=SQLAlchemyExportRepository(database.session_factory),
        export_dir=settings.export_dir,
        chunk_size=settings.export_chunk_size,
    )
    service.export(package_id, package_build_id)


def _execute_delivery(
    settings: Settings,
    database: Database,
    packages: SQLAlchemyPackageRepository,
    delivery_id: str,
) -> None:
    deliveries = SQLAlchemyDeliveryRepository(database.session_factory)
    try:
        deliveries = SQLAlchemyDeliveryRepository(
            database.session_factory,
            checkpoint_cipher=build_checkpoint_cipher(settings),
        )
        provider = build_object_storage_provider(settings)
    except Exception as exc:
        deliveries.fail(
            delivery_id,
            code=str(getattr(exc, "code", "delivery_provider_configuration_failed")),
            message=str(exc),
        )
        raise
    service = DeliveryService(
        packages=packages,
        builds=SQLAlchemyPackageBuildRepository(database.session_factory),
        reviews=SQLAlchemyQARepository(database.session_factory),
        deliveries=deliveries,
        provider=provider,
    )
    service.deliver(delivery_id)
