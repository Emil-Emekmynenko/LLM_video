import math

from media_factory.domain.models import Severity, Transcript, ValidationIssue


def validate_transcript(
    transcript: Transcript,
    *,
    duration_tolerance: float = 0.5,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    words = [word for segment in transcript.segments for word in segment.words]

    if not words:
        return [
            ValidationIssue(
                code="no_word_timings",
                severity=Severity.ERROR,
                path="transcript.segments[*].words",
                message="Транскрипт не содержит обязательных таймингов слов.",
                blocking=True,
            )
        ]

    previous_start = -1.0
    maximum_end = transcript.media_duration + duration_tolerance
    for segment_index, segment in enumerate(transcript.segments):
        for word_index, word in enumerate(segment.words):
            path = f"transcript.segments[{segment_index}].words[{word_index}]"
            invalid_number = not all(math.isfinite(value) for value in (word.start, word.end))
            invalid_bounds = word.start < 0 or word.end < word.start or word.end > maximum_end
            invalid_order = word.start < previous_start
            if not word.word.strip() or invalid_number or invalid_bounds or invalid_order:
                issues.append(
                    ValidationIssue(
                        code="invalid_word_timing",
                        severity=Severity.ERROR,
                        path=path,
                        message=(
                            "Слово имеет пустой текст, неверный порядок "
                            "или недопустимые тайминги."
                        ),
                        blocking=True,
                    )
                )
            previous_start = max(previous_start, word.start)

    return issues
