from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Protocol

from media_factory.domain.analysis import AnalysisClip, AnalysisRun, ClipInterval
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.providers.video_understanding import VideoUnderstandingProvider
from media_factory.services.event_timeline import merge_clip_events
from media_factory.services.scene_detection import SceneDetector
from media_factory.services.video_processing import split_scene_intervals


class ProxyGenerator(Protocol):
    def generate(self, source: Path, destination: Path) -> list[str]: ...


class ClipExtractor(Protocol):
    def extract(
        self,
        source: Path,
        destination: Path,
        interval: ClipInterval,
    ) -> list[str]: ...


class AnalysisPipelineError(RuntimeError):
    code = "analysis_pipeline_failed"


class MissingInspectionError(AnalysisPipelineError):
    code = "missing_media_inspection"


class InvalidAnalysisStateError(AnalysisPipelineError):
    code = "invalid_analysis_state"


class InvalidProviderOutputError(AnalysisPipelineError):
    code = "invalid_provider_output"


class AnalysisService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        assets: SQLAlchemyAssetRepository,
        analyses: SQLAlchemyAnalysisRepository,
        proxy_generator: ProxyGenerator,
        clip_extractor: ClipExtractor,
        scene_detector: SceneDetector,
        provider: VideoUnderstandingProvider,
        proxy_dir: Path,
        clip_dir: Path,
        max_clip_duration: float,
        clip_overlap: float,
    ) -> None:
        self.packages = packages
        self.assets = assets
        self.analyses = analyses
        self.proxy_generator = proxy_generator
        self.clip_extractor = clip_extractor
        self.scene_detector = scene_detector
        self.provider = provider
        self.proxy_dir = proxy_dir
        self.clip_dir = clip_dir
        self.max_clip_duration = max_clip_duration
        self.clip_overlap = clip_overlap

    def analyze(self, package_id: str) -> AnalysisRun:
        package = self.packages.get(package_id)
        if package.state is not PackageState.READY_FOR_ANALYSIS:
            raise InvalidAnalysisStateError(
                "video analysis requires package state ready_for_analysis"
            )
        asset = self.assets.get(package.source_asset_id)
        if asset.inspection is None:
            raise MissingInspectionError("asset has no persisted media inspection")

        package = self.packages.transition(
            package.id,
            target=PackageState.ANALYZING,
            expected_version=package.version,
        )
        run = self.analyses.create_run(
            package_id=package.id,
            source_sha256=asset.sha256,
            provider_name=self.provider.name,
            provider_version=self.provider.version,
            prompt_version=self.provider.prompt_version,
            inference_parameters=self.provider.inference_parameters,
        )

        try:
            proxy_path = self.proxy_dir / package.id / f"{run.id}.mp4"
            proxy_command = self.proxy_generator.generate(asset.stored_path, proxy_path)
            self.analyses.set_proxy(run.id, proxy_path=proxy_path, command=proxy_command)
            scenes = self.scene_detector.detect(proxy_path, asset.inspection.duration)
            intervals = split_scene_intervals(
                scenes,
                max_duration=self.max_clip_duration,
                overlap=self.clip_overlap,
            )
            analyzed_clips: list[AnalysisClip] = []
            for index, interval in enumerate(intervals, start=1):
                clip_path = self.clip_dir / package.id / run.id / f"clip-{index:04d}.mp4"
                extraction_command = self.clip_extractor.extract(
                    proxy_path,
                    clip_path,
                    interval,
                )
                inference_started = perf_counter()
                result = self.provider.analyze_clip(clip_path, interval)
                inference_seconds = perf_counter() - inference_started
                self._validate_provider_result(interval, result.events)
                self._validate_chapters(interval, result.suggested_chapters)
                analyzed_clips.append(
                    self.analyses.add_clip(
                        run_id=run.id,
                        interval=interval,
                        clip_path=clip_path,
                        extraction_command=extraction_command,
                        result=result,
                        inference_seconds=inference_seconds,
                    )
                )
            self.analyses.save_events(merge_clip_events(run.id, analyzed_clips))
            run = self.analyses.succeed(run.id)
            self.packages.transition(
                package.id,
                target=PackageState.AWAITING_METADATA_REVIEW,
                expected_version=package.version,
            )
            return run
        except Exception as exc:
            code = getattr(exc, "code", "analysis_failed")
            self.analyses.fail(run.id, code=str(code), message=str(exc))
            self.packages.transition(
                package.id,
                target=PackageState.ANALYSIS_FAILED,
                expected_version=package.version,
            )
            raise

    @staticmethod
    def _validate_provider_result(interval: ClipInterval, events: Sequence[object]) -> None:
        for event in events:
            relative_start = getattr(event, "relative_start", None)
            relative_end = getattr(event, "relative_end", None)
            if (
                not isinstance(relative_start, (int, float))
                or not isinstance(relative_end, (int, float))
                or relative_start < 0
                or relative_end < relative_start
                or relative_start > interval.duration
                or relative_end > interval.duration
            ):
                raise InvalidProviderOutputError("event timestamps exceed clip duration")

    @staticmethod
    def _validate_chapters(interval: ClipInterval, chapters: Sequence[object]) -> None:
        for chapter in chapters:
            relative_start = getattr(chapter, "relative_start", None)
            if (
                not isinstance(relative_start, (int, float))
                or relative_start < 0
                or relative_start > interval.duration
            ):
                raise InvalidProviderOutputError("chapter timestamp exceeds clip duration")
