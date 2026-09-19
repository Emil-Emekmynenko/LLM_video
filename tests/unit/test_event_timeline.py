from datetime import UTC, datetime

from media_factory.domain.analysis import (
    AnalysisClip,
    ClipAnalysis,
    ClipInterval,
    DetectedEvent,
    ReviewStatus,
)
from media_factory.services.event_timeline import merge_clip_events


def _clip(
    clip_id: str,
    start: float,
    end: float,
    events: list[DetectedEvent],
) -> AnalysisClip:
    return AnalysisClip(
        id=clip_id,
        analysis_run_id="run-1",
        interval=ClipInterval(start=start, end=end),
        clip_path=f"{clip_id}.mp4",
        result=ClipAnalysis(summary="test", events=events),
        inference_seconds=0.1,
        created_at=datetime.now(UTC),
    )


def _event(action: str, start: float, end: float, evidence: str) -> DetectedEvent:
    return DetectedEvent(
        relative_start=start,
        relative_end=end,
        actor="person_1",
        action=action,
        objects=["screwdriver"],
        evidence=evidence,
        confidence=0.9,
    )


def test_timeline_merges_overlap_and_preserves_conflicts() -> None:
    clips = [
        _clip("clip-1", 0, 15, [_event("picks_up", 13, 14, "hand lifts tool")]),
        _clip(
            "clip-2",
            13,
            28,
            [
                _event("picks_up", 0, 1, "tool leaves table"),
                _event("puts_down", 0.2, 0.8, "tool touches table"),
            ],
        ),
    ]

    events = merge_clip_events("run-1", clips)

    assert len(events) == 2
    picked_up = next(event for event in events if event.action == "picks_up")
    put_down = next(event for event in events if event.action == "puts_down")
    assert picked_up.start == 13
    assert picked_up.end == 14
    assert picked_up.source_clip_ids == ["clip-1", "clip-2"]
    assert len(picked_up.evidence) == 2
    assert picked_up.review_status is ReviewStatus.NEEDS_REVIEW
    assert put_down.id in picked_up.conflict_event_ids
    assert picked_up.id in put_down.conflict_event_ids
