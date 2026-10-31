"""Injectable time source so time-dependent logic is deterministic in tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """Manually advanced clock used by tests and demo scenarios."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> datetime:
        self._now += timedelta(**delta)
        return self._now
