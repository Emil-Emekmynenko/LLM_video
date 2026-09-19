from pathlib import Path

from media_factory.domain.analysis import (
    AnalysisRunState,
    ClipAnalysis,
    ClipInterval,
    DetectedEvent,
)
from media_factory.domain.models import MediaInspection, StoredAsset, StreamInfo
from media_factory.domain.package_state import PackageState
from media_factory.persistence.analysis_repository import SQLAlchemyAnalysisRepository
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.analysis_service import AnalysisService
from media_factory.services.scene_detection import WholeVideoSceneDetector


class FakeProxyGenerator:
    def generate(self, source: Path, destination: Path) -> list[str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
        return ["fake-ffmpeg", str(source), str(destination)]


class FakeClipExtractor:
    def extract(
        self,
        source: Path,
        destination: Path,
        interval: ClipInterval,
    ) -> list[str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
        return ["fake-ffmpeg", str(source), str(interval.start), str(destination)]


class ObservedEventProvider:
    name = "test-vlm"
    version = "test-revision"
    prompt_version = "test-prompt"
    inference_parameters: dict[str, str | int | float | bool | None] = {
        "temperature": 0
    }

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis:
        del clip_path, interval
        return ClipAnalysis(
            summary="A person picks up a tool.",
            events=[
                DetectedEvent(
                    relative_start=0,
                    relative_end=1,
                    actor="person_1",
                    action="picks_up_object",
                    objects=["tool"],
                    evidence="The hand lifts the tool.",
                    confidence=0.9,
                )
            ],
        )


def test_analysis_pipeline_persists_proxy_clips_and_results(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'analysis.sqlite3'}")
    database.create_schema()
    assets = SQLAlchemyAssetRepository(database.session_factory)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    analyses = SQLAlchemyAnalysisRepository(database.session_factory)
    asset = StoredAsset(
        id="asset-analysis",
        original_name="source.mp4",
        stored_path=tmp_path / "source.mp4",
        size_bytes=100,
        sha256="c" * 64,
        inspection=MediaInspection(
            duration=32,
            size_bytes=100,
            format_name="mov,mp4",
            streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
        ),
    )
    assets.save(asset)
    package = packages.create(asset.id)
    package = packages.transition(
        package.id,
        target=PackageState.INSPECTING,
        expected_version=package.version,
    )
    package = packages.transition(
        package.id,
        target=PackageState.READY_FOR_ANALYSIS,
        expected_version=package.version,
    )
    service = AnalysisService(
        packages=packages,
        assets=assets,
        analyses=analyses,
        proxy_generator=FakeProxyGenerator(),
        clip_extractor=FakeClipExtractor(),
        scene_detector=WholeVideoSceneDetector(),
        provider=ObservedEventProvider(),
        proxy_dir=tmp_path / "proxies",
        clip_dir=tmp_path / "clips",
        max_clip_duration=15,
        clip_overlap=2,
    )

    run = service.analyze(package.id)
    clips = analyses.list_clips(run.id)
    events = analyses.list_events(run.id)
    completed_package = packages.get(package.id)

    assert run.state is AnalysisRunState.SUCCEEDED
    assert run.source_sha256 == asset.sha256
    assert run.proxy_path is not None
    assert len(clips) == 3
    assert clips[0].interval == ClipInterval(start=0, end=15)
    assert clips[1].interval == ClipInterval(start=13, end=28)
    assert clips[2].interval == ClipInterval(start=26, end=32)
    assert clips[0].inference_seconds is not None
    assert run.inference_parameters == {"temperature": 0}
    assert len(events) == 3
    assert events[1].start == 13
    assert events[1].source_clip_ids == [clips[1].id]
    assert completed_package.state is PackageState.AWAITING_METADATA_REVIEW
