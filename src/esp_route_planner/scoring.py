"""Deterministic priority scoring for ESP wells.

Formula (each component 0–100, weighted total 0–100):

    priority_score = 0.30 * prod_score
                   + 0.25 * uplift_score
                   + 0.25 * urgency_score
                   + 0.10 * confidence_score
                   + 0.10 * recency_score

Components
----------
- **prod_score**: production criticality — higher oil rate and liquid rate → higher score.
  Normalized: min(current_oil_bpd / 500 * 100, 100).
- **uplift_score**: potential improvement in bpd.
  Normalized: min(uplift_oil_bpd / 30 * 100, 100).
- **urgency_score**: count of operational issues + frequency gap penalty + water cut trend.
  Each issue adds 15 pts; freq gap (|current - optimal|) adds up to 25 pts;
  water_cut_pct > 70 adds 20 pts. Capped at 100.
- **confidence_score**: data quality / recommendation confidence (0–1 → 0–100).
- **recency_score**: how overdue the well is for a visit.
  min(days_since_last_visit / 30 * 100, 100). Defaults to 50 if unknown.
"""

from __future__ import annotations

from .schemas import ScoreBreakdown, Well

# Weights
W_PROD = 0.30
W_UPLIFT = 0.25
W_URGENCY = 0.25
W_CONFIDENCE = 0.10
W_RECENCY = 0.10


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(v, hi))


def score_well(well: Well) -> ScoreBreakdown:
    """Compute a deterministic priority score with full breakdown."""

    # Production criticality
    prod_score = _clamp(well.current_oil_bpd / 500.0 * 100.0)

    # Uplift potential
    uplift_score = _clamp(well.uplift_oil_bpd / 30.0 * 100.0)

    # Urgency — issues + frequency gap + high water cut
    urgency = len(well.issues) * 15.0
    if (
        well.freq_current_hz is not None
        and well.freq_optimal_hz is not None
    ):
        freq_gap = abs(well.freq_current_hz - well.freq_optimal_hz)
        urgency += min(freq_gap / 5.0 * 25.0, 25.0)
    if well.water_cut_pct > 70:
        urgency += 20.0
    urgency_score = _clamp(urgency)

    # Confidence in recommendation
    confidence_score = _clamp(well.confidence * 100.0)

    # Recency — days since last visit
    if well.days_since_last_visit is not None:
        recency_score = _clamp(well.days_since_last_visit / 30.0 * 100.0)
    else:
        recency_score = 50.0  # unknown → neutral

    priority_score = round(
        W_PROD * prod_score
        + W_UPLIFT * uplift_score
        + W_URGENCY * urgency_score
        + W_CONFIDENCE * confidence_score
        + W_RECENCY * recency_score,
        2,
    )

    return ScoreBreakdown(
        well_id=well.well_id,
        prod_score=round(prod_score, 2),
        uplift_score=round(uplift_score, 2),
        urgency_score=round(urgency_score, 2),
        confidence_score=round(confidence_score, 2),
        recency_score=round(recency_score, 2),
        priority_score=priority_score,
    )
