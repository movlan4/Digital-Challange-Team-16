"""Anomaly detection: an individual bus that stays overcrowded (HIGH).

Rule (challenge brief): HIGH congestion recorded continuously for 10 minutes
-> operator alert "Additional bus reserve must be allocated" for that route.

Density is measured per bus (passenger reports fused with GPS telemetry) and
sampled periodically, so the rule is evaluated per bus over those samples.
Because the signal is noisy and can have gaps, "continuous" uses time buckets:

* The look-back period is split into fixed buckets (default 2 min), walking
  backwards from ``now``.
* A bucket is *HIGH* when it contains at least one observation and the share
  of HIGH observations in it is >= ``high_ratio`` (default 60%).
* The HIGH streak is the run of consecutive HIGH buckets ending at ``now``.
  An empty bucket (no signal) or a non-HIGH bucket breaks the streak.
* ``high_since`` is the earliest HIGH observation inside the streak; the
  anomaly fires when ``now - high_since >= window``.

Alerts are never closed automatically: an alert stays open (ACTIVE or
ACKNOWLEDGED) until an operator resolves it. After a manual resolution a
route needs a *fresh* 10-minute streak before it can alert again.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, NamedTuple, Optional, Sequence

from app.models import Alert, CongestionLevel, bus_index

log = logging.getLogger(__name__)

ALERT_MESSAGE = "Additional bus reserve must be allocated"


class Observation(NamedTuple):
    created_at: datetime
    level: CongestionLevel


@dataclass(frozen=True)
class AnomalyResult:
    is_anomaly: bool
    high_streak_minutes: float
    high_since: Optional[datetime]


NO_ANOMALY = AnomalyResult(False, 0.0, None)


class ContinuousHighDetector:
    def __init__(self, window_minutes: float = 10, bucket_minutes: float = 2, high_ratio: float = 0.6) -> None:
        if bucket_minutes <= 0 or window_minutes <= 0:
            raise ValueError("window and bucket sizes must be positive")
        if not 0 < high_ratio <= 1:
            raise ValueError("high_ratio must be in (0, 1]")
        self.window = timedelta(minutes=window_minutes)
        self.bucket = timedelta(minutes=bucket_minutes)
        self.high_ratio = high_ratio

    @property
    def lookback(self) -> timedelta:
        """How much history callers must keep (window plus one spare bucket)."""
        return self.window + self.bucket

    def evaluate(self, observations: Sequence[Observation], now: datetime) -> AnomalyResult:
        """``observations`` are anything with ``created_at`` and ``level`` attributes."""
        relevant = [o for o in observations if o.created_at <= now]
        high_since: Optional[datetime] = None
        bucket_end = now

        while True:
            bucket_start = bucket_end - self.bucket
            in_bucket = [o for o in relevant if bucket_start < o.created_at <= bucket_end]
            highs = [o for o in in_bucket if o.level is CongestionLevel.HIGH]
            if not in_bucket or len(highs) / len(in_bucket) < self.high_ratio:
                break
            high_since = min(o.created_at for o in highs)
            if now - high_since >= self.window:
                break  # threshold already proven; no need to scan further back
            bucket_end = bucket_start

        if high_since is None:
            return NO_ANOMALY
        streak = now - high_since
        return AnomalyResult(streak >= self.window, round(streak.total_seconds() / 60, 2), high_since)


class AnomalyMonitor:
    """Background job: evaluates every bus with density observations; at most one open alert per route."""

    def __init__(
        self,
        repository,
        network,
        detector: ContinuousHighDetector,
        clock,
        observations: Callable[[datetime], dict[str, list]],
        route_of: Callable[[str], Optional[str]],
        on_change: Optional[Callable[[Alert], Awaitable[None]]] = None,
    ) -> None:
        """
        observations(since) -> {bus_id: density observations since ``since``}
        route_of(bus_id) -> route id of the bus
        """
        self._repo = repository
        self._network = network
        self._detector = detector
        self._clock = clock
        self._observations = observations
        self._route_of = route_of
        self._on_change = on_change
        self.last_results: dict[str, AnomalyResult] = {}

    async def run_once(self) -> list[Alert]:
        """Single scan. Returns newly created alerts."""
        now = self._clock.now()
        since = now - self._detector.lookback
        created: list[Alert] = []
        open_routes = {a.route_id for a in self._repo.open_alerts()}
        last_resolved = self._repo.last_resolved_alert_times()
        results: dict[str, AnomalyResult] = {}

        for bus_id, reports in self._observations(since).items():
            route_id = self._route_of(bus_id)
            if route_id is None:
                continue
            not_before = last_resolved.get(route_id)  # a resolved alert needs a fresh streak
            relevant = [r for r in reports if not_before is None or r.created_at > not_before]
            result = self._detector.evaluate(relevant, now)
            results[bus_id] = result
            if not result.is_anomaly or route_id in open_routes:
                continue
            route = self._network.route(route_id)
            message = (
                f"Line {route.number}, bus {bus_index(bus_id)} has been overcrowded (HIGH) for "
                f"{result.high_streak_minutes:.0f}+ min. {ALERT_MESSAGE}."
            )
            alert = self._repo.create_alert(route_id, bus_id, message, result.high_since, now)
            open_routes.add(route_id)
            log.warning("ALERT raised: %s", message)
            created.append(alert)

        self.last_results = results
        if self._on_change:
            for alert in created:
                await self._on_change(alert)
        return created
