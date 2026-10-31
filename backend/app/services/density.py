"""Hybrid per-bus crowdedness: passenger-report consensus fused with GPS telemetry.

A single passenger report never changes a bus's status. Reports are treated
as votes and only count once several passengers agree:

1. **One passenger = one vote.** Within the time window only each passenger's
   latest report on a bus counts, so repeated reports cannot fake consensus.
2. **Recency weight.** A vote's weight halves every 5 minutes.
3. **Threshold.** The reports affect the status only when at least
   ``min_reporters`` different passengers voted *and* their combined weight
   reaches ``min_weight`` (i.e. the votes are recent enough). Until then the
   reports are "pending" and the status comes from GPS alone (or stays empty).
4. **Outlier rejection.** Votes two levels away from the weighted median
   (e.g. a lone HIGH among LOWs) are ignored when they are a small minority
   (< 40 % of the vote weight).
5. **Weighted consensus.** The remaining votes are averaged by weight
   (LOW=1, MEDIUM=2, HIGH=3).
6. **Confidence ramp.** How strongly the consensus overrides the GPS estimate
   grows with the evidence: confidence = kept weight / ``full_confidence_weight``
   (max 1). Density therefore rises gradually as more passengers agree.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from app.models import CongestionLevel

RECENCY_HALF_LIFE_MIN = 5.0
OUTLIER_LEVEL_DISTANCE = 2   # LOW vs HIGH
OUTLIER_MAX_SHARE = 0.4      # far-off votes are only ignored while they are a small minority
# At full confidence a passenger consensus outweighs the GPS inference 85/15, so enough
# agreeing passengers can lift a bus to HIGH even when its GPS behaviour still looks normal.
REPORT_WEIGHT = 0.85


@dataclass(frozen=True)
class ReportEstimate:
    score: Optional[float]           # consensus score; None until the threshold is reached
    confidence: float                # 0..1, weight of the consensus in the fusion
    report_count: int                # all reports on the bus in the window
    reporters: int                   # distinct passengers (votes)
    consensus: bool                  # threshold reached
    outliers: int                    # votes ignored as outliers
    last_report_at: Optional[datetime]


NO_REPORTS = ReportEstimate(None, 0.0, 0, 0, False, 0, None)


@dataclass(frozen=True)
class HybridEstimate:
    level: Optional[CongestionLevel]
    score: Optional[float]
    source: Optional[str]  # "reports+gps" | "reports" | "gps" | None (no data)


def _weighted_median(votes: list[tuple[float, int]]) -> int:
    """votes = [(weight, score)]; the score at which half of the weight is reached."""
    total = sum(w for w, _ in votes)
    running = 0.0
    for weight, score in sorted(votes, key=lambda v: v[1]):
        running += weight
        if running >= total / 2:
            return score
    return votes[-1][1]


class DensityEstimator:
    def __init__(self, window_minutes: float = 15, min_reporters: int = 3, min_weight: float = 1.5,
                 full_confidence_weight: float = 4.0) -> None:
        self.window = timedelta(minutes=window_minutes)
        self.min_reporters = min_reporters
        self.min_weight = min_weight
        self.full_confidence_weight = full_confidence_weight

    def _weight(self, created_at: datetime, now: datetime) -> float:
        return 0.5 ** (((now - created_at).total_seconds() / 60) / RECENCY_HALF_LIFE_MIN)

    def estimate(self, reports: Sequence, now: datetime) -> ReportEstimate:
        """``reports``: one bus's reports (objects with created_at, level, passenger_email)."""
        recent = sorted((r for r in reports if now - self.window <= r.created_at <= now), key=lambda r: r.created_at)
        if not recent:
            return NO_REPORTS
        latest_by_passenger = {r.passenger_email: r for r in recent}  # sorted -> last one wins
        votes = [(self._weight(r.created_at, now), r.level.score) for r in latest_by_passenger.values()]
        evidence = sum(w for w, _ in votes)
        last = recent[-1].created_at

        if len(votes) < self.min_reporters or evidence < self.min_weight:
            return ReportEstimate(None, 0.0, len(recent), len(votes), False, 0, last)

        median = _weighted_median(votes)
        far = [v for v in votes if abs(v[1] - median) >= OUTLIER_LEVEL_DISTANCE]
        kept = votes
        if far and sum(w for w, _ in far) < OUTLIER_MAX_SHARE * evidence:
            kept = [v for v in votes if abs(v[1] - median) < OUTLIER_LEVEL_DISTANCE]
        kept_weight = sum(w for w, _ in kept)
        score = sum(w * s for w, s in kept) / kept_weight
        confidence = min(1.0, kept_weight / self.full_confidence_weight)
        return ReportEstimate(round(score, 2), round(confidence, 2), len(recent), len(votes), True,
                              len(votes) - len(kept), last)


def fuse(report_score: Optional[float], gps_score: Optional[float], report_confidence: float = 1.0) -> HybridEstimate:
    """Combine both signals; either one alone is used when the other is missing.

    ``report_score`` is only set once the passenger consensus threshold is reached;
    its share grows with ``report_confidence`` up to REPORT_WEIGHT.
    """
    if report_score is not None and gps_score is not None:
        report_share = REPORT_WEIGHT * report_confidence
        score, source = report_share * report_score + (1 - report_share) * gps_score, "reports+gps"
    elif report_score is not None:
        score, source = report_score, "reports"
    elif gps_score is not None:
        score, source = gps_score, "gps"
    else:
        return HybridEstimate(None, None, None)
    return HybridEstimate(CongestionLevel.from_score(score), round(score, 2), source)
