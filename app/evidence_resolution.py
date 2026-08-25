from __future__ import annotations

from app.domain import Assumption, Confidence, Evidence, Venture
from app.source_router import source_strength


CONFIDENCE_WEIGHT = {
    Confidence.UNKNOWN: 0.0,
    Confidence.LOW: 0.5,
    Confidence.MEDIUM: 1.0,
    Confidence.HIGH: 1.5,
    Confidence.VERIFIED: 2.0,
}


def evidence_score(item: Evidence) -> tuple[float, float]:
    """Rank evidence deterministically before it can drive a canonical model input.

    Source class dominates confidence. Confidence breaks ties inside a source class. This prevents a
    high-confidence founder guess or model inference from displacing a current quote/official record while
    still allowing stronger independent evidence to replace a weak prior.
    """

    return (
        source_strength(item.evidence_type) * 10.0 + CONFIDENCE_WEIGHT[item.confidence],
        item.observed_at.timestamp(),
    )


def reconcile_assumption(venture: Venture, assumption: Assumption) -> Assumption:
    candidates = [
        item
        for item in venture.evidence
        if not item.stale and item.assumption_key == assumption.key
    ]
    if not candidates:
        return assumption

    strongest = max(candidates, key=evidence_score)
    assumption.confidence = strongest.confidence
    assumption.source_note = strongest.claim
    assumption.updated_at = strongest.observed_at

    numeric = [
        item
        for item in candidates
        if isinstance(item.value, (int, float)) and not isinstance(item.value, bool)
    ]
    if numeric:
        strongest_numeric = max(numeric, key=evidence_score)
        assumption.value = float(strongest_numeric.value)
        assumption.unit = strongest_numeric.unit or assumption.unit
        assumption.source_note = strongest_numeric.claim
        assumption.confidence = strongest_numeric.confidence
        assumption.updated_at = strongest_numeric.observed_at
    return assumption


def reconcile_venture(venture: Venture) -> Venture:
    for assumption in venture.assumptions:
        reconcile_assumption(venture, assumption)
    return venture
