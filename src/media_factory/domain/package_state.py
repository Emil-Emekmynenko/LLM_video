from enum import StrEnum


class PackageState(StrEnum):
    DRAFT = "draft"
    UPLOADED = "uploaded"
    INSPECTING = "inspecting"
    INSPECTION_FAILED = "inspection_failed"
    DUPLICATE_REVIEW = "duplicate_review"
    READY_FOR_ANALYSIS = "ready_for_analysis"
    ANALYZING = "analyzing"
    ANALYSIS_FAILED = "analysis_failed"
    AWAITING_METADATA_REVIEW = "awaiting_metadata_review"
    MASTER_READY = "master_ready"
    TRANSCRIBING = "transcribing"
    TRANSCRIPTION_FAILED = "transcription_failed"
    ALIGNING = "aligning"
    ALIGNMENT_FAILED = "alignment_failed"
    PACKAGING = "packaging"
    VALIDATION_FAILED = "validation_failed"
    AWAITING_QA = "awaiting_qa"
    VALIDATED = "validated"
    DELIVERY_QUEUED = "delivery_queued"
    UPLOADING_MEDIA = "uploading_media"
    UPLOADING_SIDECARS = "uploading_sidecars"
    VERIFYING_DELIVERY = "verifying_delivery"
    DELIVERY_FAILED = "delivery_failed"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS: dict[PackageState, frozenset[PackageState]] = {
    PackageState.DRAFT: frozenset({PackageState.UPLOADED, PackageState.CANCELLED}),
    PackageState.UPLOADED: frozenset({PackageState.INSPECTING, PackageState.CANCELLED}),
    PackageState.INSPECTING: frozenset(
        {
            PackageState.INSPECTION_FAILED,
            PackageState.DUPLICATE_REVIEW,
            PackageState.READY_FOR_ANALYSIS,
        }
    ),
    PackageState.INSPECTION_FAILED: frozenset(
        {PackageState.INSPECTING, PackageState.CANCELLED}
    ),
    PackageState.DUPLICATE_REVIEW: frozenset(
        {PackageState.READY_FOR_ANALYSIS, PackageState.CANCELLED}
    ),
    PackageState.READY_FOR_ANALYSIS: frozenset(
        {PackageState.ANALYZING, PackageState.CANCELLED}
    ),
    PackageState.ANALYZING: frozenset(
        {PackageState.ANALYSIS_FAILED, PackageState.AWAITING_METADATA_REVIEW}
    ),
    PackageState.ANALYSIS_FAILED: frozenset(
        {PackageState.ANALYZING, PackageState.CANCELLED}
    ),
    PackageState.AWAITING_METADATA_REVIEW: frozenset(
        {PackageState.MASTER_READY, PackageState.CANCELLED}
    ),
    PackageState.MASTER_READY: frozenset({PackageState.TRANSCRIBING}),
    PackageState.TRANSCRIBING: frozenset(
        {PackageState.TRANSCRIPTION_FAILED, PackageState.ALIGNING}
    ),
    PackageState.TRANSCRIPTION_FAILED: frozenset(
        {PackageState.TRANSCRIBING, PackageState.CANCELLED}
    ),
    PackageState.ALIGNING: frozenset({PackageState.ALIGNMENT_FAILED, PackageState.PACKAGING}),
    PackageState.ALIGNMENT_FAILED: frozenset(
        {PackageState.ALIGNING, PackageState.CANCELLED}
    ),
    PackageState.PACKAGING: frozenset(
        {PackageState.VALIDATION_FAILED, PackageState.AWAITING_QA}
    ),
    PackageState.VALIDATION_FAILED: frozenset(
        {PackageState.PACKAGING, PackageState.CANCELLED}
    ),
    PackageState.AWAITING_QA: frozenset(
        {PackageState.VALIDATED, PackageState.VALIDATION_FAILED}
    ),
    PackageState.VALIDATED: frozenset({PackageState.DELIVERY_QUEUED}),
    PackageState.DELIVERY_QUEUED: frozenset({PackageState.UPLOADING_MEDIA}),
    PackageState.UPLOADING_MEDIA: frozenset(
        {PackageState.UPLOADING_SIDECARS, PackageState.DELIVERY_FAILED}
    ),
    PackageState.UPLOADING_SIDECARS: frozenset(
        {PackageState.VERIFYING_DELIVERY, PackageState.DELIVERY_FAILED}
    ),
    PackageState.VERIFYING_DELIVERY: frozenset(
        {PackageState.COMPLETE, PackageState.DELIVERY_FAILED}
    ),
    PackageState.DELIVERY_FAILED: frozenset(
        {PackageState.DELIVERY_QUEUED, PackageState.CANCELLED}
    ),
    PackageState.COMPLETE: frozenset(),
    PackageState.CANCELLED: frozenset(),
}


class InvalidPackageTransition(ValueError):
    def __init__(self, current: PackageState, target: PackageState) -> None:
        super().__init__(f"Package cannot transition from {current.value} to {target.value}")
        self.current = current
        self.target = target


def ensure_transition_allowed(current: PackageState, target: PackageState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidPackageTransition(current, target)
