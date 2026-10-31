"""GPS-driven crowd estimation.

Infers how crowded a bus is from its GPS behaviour alone - no passenger input:

* **Dwell time** - long stops mean many passengers boarding / alighting.
* **Speed** - slow movement between stops (traffic, crowded boarding) adds delay.
* **Clustering / headway** - a bus with a large gap to the bus *ahead* of it
  on the same line picks up everyone who has been waiting since that bus, so
  it fills up; a bus bunched right behind another runs nearly empty.

Every feature is computable from raw AVL/GPS positions, so this works the same
with a real fleet feed. The estimate is fused with passenger reports in
``services.density``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FREE_FLOW_KMH = 24.0
BASELINE_DWELL_S = 30.0   # typical off-peak stop duration
MIN_STOPS_OBSERVED = 2   # need a couple of stops before dwell behaviour means anything

# Weights of the three GPS signals in the crowding index (sum = 1)
DWELL_WEIGHT, SPEED_WEIGHT, GAP_WEIGHT = 0.5, 0.2, 0.3


@dataclass(frozen=True)
class BusTelemetry:
    avg_dwell_s: Optional[float]     # mean stop duration over the last few stops
    moving_speed_kmh: float          # smoothed speed while moving
    gap_ratio: Optional[float]       # gap to the bus ahead / even spacing (1.0 = on schedule)
    stops_observed: int


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def gps_crowd_score(t: BusTelemetry) -> Optional[float]:
    """Crowding on the 1..3 scale (LOW..HIGH) from GPS behaviour, or None if not enough data."""
    if t.stops_observed < MIN_STOPS_OBSERVED or t.avg_dwell_s is None:
        return None
    dwell = _clamp((t.avg_dwell_s / BASELINE_DWELL_S - 1) / 1.5)    # 2.5x the baseline dwell -> 1
    slow = _clamp((1 - t.moving_speed_kmh / FREE_FLOW_KMH) / 0.5)    # half the free-flow speed -> 1
    gap = _clamp(t.gap_ratio - 1) if t.gap_ratio is not None else 0.0  # twice the scheduled gap -> 1
    index = DWELL_WEIGHT * dwell + SPEED_WEIGHT * slow + GAP_WEIGHT * gap
    return round(1 + 2 * index, 2)
