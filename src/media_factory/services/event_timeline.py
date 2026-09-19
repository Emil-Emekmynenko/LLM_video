from uuid import NAMESPACE_URL, uuid5

from media_factory.domain.analysis import AnalysisClip, ReviewStatus, TimelineEvent


def merge_clip_events(run_id: str, clips: list[AnalysisClip]) -> list[TimelineEvent]:
    """Build an absolute timeline while retaining every source clip as provenance."""

    timeline: list[TimelineEvent] = []
    for clip in clips:
        for event in clip.result.events:
            candidate = TimelineEvent(
                id=_event_id(
                    run_id,
                    clip.id,
                    event.relative_start,
                    event.relative_end,
                    event.actor,
                    event.action,
                ),
                analysis_run_id=run_id,
                start=clip.interval.start + event.relative_start,
                end=clip.interval.start + event.relative_end,
                actor=event.actor,
                action=event.action,
                objects=sorted(set(event.objects)),
                evidence=[event.evidence],
                confidence=event.confidence,
                source_clip_ids=[clip.id],
            )
            duplicate = next(
                (existing for existing in timeline if _same_observation(existing, candidate)),
                None,
            )
            if duplicate is None:
                timeline.append(candidate)
            else:
                _merge_duplicate(duplicate, candidate)

    _mark_conflicts(timeline)
    return sorted(timeline, key=lambda event: (event.start, event.end, event.id))


def _same_observation(left: TimelineEvent, right: TimelineEvent) -> bool:
    return (
        _normalized(left.actor) == _normalized(right.actor)
        and _normalized(left.action) == _normalized(right.action)
        and {_normalized(value) for value in left.objects}
        == {_normalized(value) for value in right.objects}
        and _overlaps(left, right)
    )


def _merge_duplicate(target: TimelineEvent, source: TimelineEvent) -> None:
    target.start = min(target.start, source.start)
    target.end = max(target.end, source.end)
    target.confidence = max(target.confidence, source.confidence)
    target.source_clip_ids = sorted(set(target.source_clip_ids + source.source_clip_ids))
    target.evidence = list(dict.fromkeys(target.evidence + source.evidence))


def _mark_conflicts(events: list[TimelineEvent]) -> None:
    for index, left in enumerate(events):
        for right in events[index + 1 :]:
            if (
                _normalized(left.actor) == _normalized(right.actor)
                and _normalized(left.action) != _normalized(right.action)
                and _overlaps(left, right)
            ):
                left.conflict_event_ids.append(right.id)
                right.conflict_event_ids.append(left.id)
                left.review_status = ReviewStatus.NEEDS_REVIEW
                right.review_status = ReviewStatus.NEEDS_REVIEW


def _overlaps(left: TimelineEvent, right: TimelineEvent) -> bool:
    return max(left.start, right.start) <= min(left.end, right.end)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _event_id(
    run_id: str,
    clip_id: str,
    relative_start: float,
    relative_end: float,
    actor: str,
    action: str,
) -> str:
    source = (
        f"{run_id}:{clip_id}:{relative_start:.6f}:{relative_end:.6f}:"
        f"{_normalized(actor)}:{_normalized(action)}"
    )
    return str(uuid5(NAMESPACE_URL, source))
