from media_factory.domain.models import Transcript, TranscriptSegment, TranscriptWord
from media_factory.services.transcript_validator import validate_transcript


def make_transcript(words: list[TranscriptWord]) -> Transcript:
    return Transcript(
        asset_id="asset-1",
        master_sha256="a" * 64,
        media_duration=10,
        segments=[TranscriptSegment(start=0, end=10, text="hello", words=words)],
    )


def test_missing_words_is_blocking() -> None:
    issues = validate_transcript(make_transcript([]))

    assert [issue.code for issue in issues] == ["no_word_timings"]
    assert issues[0].blocking is True


def test_valid_word_timings_pass() -> None:
    transcript = make_transcript(
        [
            TranscriptWord(word="Hello", start=0.1, end=0.4),
            TranscriptWord(word="world", start=0.5, end=0.9),
        ]
    )

    assert validate_transcript(transcript) == []


def test_out_of_order_word_is_rejected() -> None:
    transcript = make_transcript(
        [
            TranscriptWord(word="Second", start=2.0, end=2.4),
            TranscriptWord(word="First", start=1.0, end=1.3),
        ]
    )

    assert [issue.code for issue in validate_transcript(transcript)] == [
        "invalid_word_timing"
    ]

