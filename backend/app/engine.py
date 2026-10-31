"""Application service layer: wires the domain services together.

API routers stay thin and only call methods on :class:`TransitEngine`.
"""
from __future__ import annotations

import asyncio
import logging
import random
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

from app.config import Settings
from app.models import (
    Alert, AlertStatus, Arrival, BusDensity, BusPosition, CongestionLevel, Identity, Report, ReportIn,
    ReportReceipt, ReportStatus, ReportUpdateIn, RouteDensity,
)
from app.repository import Repository
from app.services.anomaly import AnomalyMonitor, ContinuousHighDetector, Observation
from app.services.auth import AuthService
from app.services.clock import SystemClock
from app.services.density import NO_REPORTS, DensityEstimator, fuse
from app.services.events import OPERATOR, PUBLIC, EventBroadcaster
from app.services.mailer import build_mailer
from app.services.network import TransitNetwork
from app.services.simulation import BusFleet, SimBus
from app.services.telemetry import BusTelemetry, gps_crowd_score

log = logging.getLogger(__name__)

# Allowed manual report transitions (reports are never closed automatically)
REPORT_TRANSITIONS = {
    ReportStatus.OPEN: {ReportStatus.IN_PROGRESS, ReportStatus.RESOLVED},
    ReportStatus.IN_PROGRESS: {ReportStatus.RESOLVED},
    ReportStatus.RESOLVED: set(),
}


class DomainError(Exception):
    """Business-rule violation; mapped to an HTTP status by the API layer."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class TransitEngine:
    def __init__(
        self,
        settings: Settings,
        network: Optional[TransitNetwork] = None,
        clock=None,
        rng: Optional[random.Random] = None,
        mailer=None,
    ) -> None:
        self.settings = settings
        self.clock = clock or SystemClock()
        self.network = network or TransitNetwork.from_file(settings.network_path)
        self.repo = Repository(settings.database_path, {r.id: r.number for r in self.network.routes})
        self.mailer = mailer or build_mailer(settings)
        self.auth = AuthService(self.repo, self.clock, self.mailer)
        self.fleet = BusFleet(self.network, settings.simulation_speed_multiplier, rng, settings.rush_hour_mode)
        self.events = EventBroadcaster()
        self.density = DensityEstimator(
            settings.density_window_minutes, settings.report_consensus_min_reporters,
            settings.report_consensus_min_weight, settings.report_full_confidence_weight,
        )
        self.detector = ContinuousHighDetector(
            settings.anomaly_window_minutes, settings.anomaly_bucket_minutes, settings.anomaly_high_ratio,
        )
        history_len = int(self.detector.lookback.total_seconds() / settings.anomaly_check_interval_seconds) + 20
        self._history: dict[str, deque[Observation]] = defaultdict(lambda: deque(maxlen=history_len))
        self.monitor = AnomalyMonitor(
            self.repo, self.network, self.detector, self.clock,
            observations=self._density_history,
            route_of=lambda bus_id: bus.route_id if (bus := self.fleet.get(bus_id)) else None,
            on_change=self._publish_alert,
        )
        self._tasks: list[asyncio.Task] = []
        self._seed_admin()

    def _seed_admin(self) -> None:
        password = self.settings.admin_password
        if not password:
            password = secrets.token_urlsafe(12)
            log.warning("No ADMIN_PASSWORD set - generated master admin password for '%s': %s",
                        self.settings.admin_email, password)
        self.auth.ensure_admin(self.settings.admin_email, password)

    # ---------- Hybrid density: passenger reports + GPS telemetry ----------

    def _reports_by_bus(self, since: datetime) -> dict[str, list[Report]]:
        grouped: dict[str, list[Report]] = defaultdict(list)
        for r in self.repo.reports_since(since):
            grouped[r.bus_id].append(r)
        return grouped

    def telemetry(self, bus: SimBus) -> BusTelemetry:
        dwells = [d for *_, d in bus.dwells]
        return BusTelemetry(
            avg_dwell_s=round(sum(dwells) / len(dwells), 1) if dwells else None,
            moving_speed_kmh=round(bus.moving_speed_kmh, 1),
            gap_ratio=self.fleet.gap_ahead_ratio(bus),
            stops_observed=len(dwells),
        )

    def bus_densities(self, now: Optional[datetime] = None) -> dict[str, BusDensity]:
        now = now or self.clock.now()
        reports = self._reports_by_bus(now - self.density.window)
        result = {}
        for bus in self.fleet.all():
            bus_reports = reports.get(bus.bus_id)
            rep = self.density.estimate(bus_reports, now) if bus_reports else NO_REPORTS
            tel = self.telemetry(bus)
            gps = gps_crowd_score(tel)
            hybrid = fuse(rep.score, gps, rep.confidence)
            streak = self.monitor.last_results.get(bus.bus_id)
            result[bus.bus_id] = BusDensity(
                bus_id=bus.bus_id, route_id=bus.route_id, route_number=bus.route_number,
                level=hybrid.level, score=hybrid.score, source=hybrid.source,
                report_score=rep.score, report_count=rep.report_count, last_report_at=rep.last_report_at,
                reporters=rep.reporters, consensus_reached=rep.consensus,
                reporters_needed=self.settings.report_consensus_min_reporters,
                report_confidence=rep.confidence, outlier_reports=rep.outliers,
                gps_score=gps, avg_dwell_s=tel.avg_dwell_s, moving_speed_kmh=tel.moving_speed_kmh,
                gap_ratio=round(tel.gap_ratio, 2) if tel.gap_ratio is not None else None,
                dwelling=bus.dwelling,
                high_streak_minutes=streak.high_streak_minutes if streak else 0.0,
                anomaly_threshold_minutes=self.settings.anomaly_window_minutes,
            )
        return result

    def route_densities(self, densities: Optional[dict[str, BusDensity]] = None) -> list[RouteDensity]:
        """Line crowding = mean hybrid score of the line's buses that have an estimate."""
        densities = densities or self.bus_densities()
        by_route: dict[str, list[BusDensity]] = defaultdict(list)
        for d in densities.values():
            by_route[d.route_id].append(d)
        result = []
        for route in self.network.routes:
            buses = by_route.get(route.id, [])
            scored = [d.score for d in buses if d.score is not None]
            dwells = [d.avg_dwell_s for d in buses if d.avg_dwell_s is not None]
            score = round(sum(scored) / len(scored), 2) if scored else None
            result.append(RouteDensity(
                route_id=route.id, route_number=route.number, route_name=route.name,
                level=CongestionLevel.from_score(score) if score is not None else None, score=score,
                bus_count=len(buses), high_bus_count=sum(1 for d in buses if d.level is CongestionLevel.HIGH),
                report_count=sum(d.report_count for d in buses),
                avg_dwell_s=round(sum(dwells) / len(dwells), 1) if dwells else None,
            ))
        return result

    def _density_history(self, since: datetime) -> dict[str, list[Observation]]:
        return {bus_id: [o for o in obs if o.created_at >= since] for bus_id, obs in self._history.items()}

    def record_density_samples(self, now: datetime) -> None:
        """Sample each bus's hybrid level; this time series feeds the 10-minute alert rule."""
        for bus_id, d in self.bus_densities(now).items():
            if d.level is not None:
                self._history[bus_id].append(Observation(now, d.level))

    # ---------- Queries ----------

    def _position(self, bus: SimBus, density: Optional[BusDensity]) -> BusPosition:
        lat, lng, next_stop = self.fleet.location(bus)
        return BusPosition(
            bus_id=bus.bus_id, route_id=bus.route_id, route_number=bus.route_number,
            lat=round(lat, 6), lng=round(lng, 6), speed_kmh=round(bus.speed_kmh, 1), next_stop_id=next_stop,
            dwelling=bus.dwelling, level=density.level if density else None,
            density_source=density.source if density else None,
            report_count=density.report_count if density else 0, updated_at=self.fleet.updated_at,
        )

    def positions(self, route_id: Optional[str] = None) -> list[BusPosition]:
        densities = self.bus_densities()
        return [self._position(b, densities.get(b.bus_id)) for b in self.fleet.all(route_id)]

    def bus(self, bus_id: str) -> BusPosition:
        bus = self.fleet.get(bus_id)
        if bus is None:
            raise DomainError(f"Unknown bus '{bus_id}'", 404)
        return self._position(bus, self.bus_densities().get(bus_id))

    def arrivals(self, stop_id: str, route_id: Optional[str] = None, limit: int = 20) -> list[Arrival]:
        """Every bus heading to ``stop_id`` (all lines serving it), soonest first."""
        if self.network.stop(stop_id) is None:
            raise DomainError(f"Unknown stop '{stop_id}'", 404)
        densities = self.bus_densities()
        result = []
        for bus in self.fleet.buses_for_stop(stop_id, route_id):
            eta = self.fleet.eta(bus, stop_id)
            if eta is None:
                continue
            distance, seconds = eta
            d = densities[bus.bus_id]
            result.append(Arrival(
                bus_id=bus.bus_id, route_id=bus.route_id, route_number=bus.route_number, stop_id=stop_id,
                eta_seconds=int(seconds), eta_minutes=round(seconds / 60, 1), distance_m=int(distance),
                dwelling=bus.dwelling, level=d.level, density_source=d.source, report_count=d.report_count,
            ))
        result.sort(key=lambda a: a.eta_seconds)
        return result[:limit]

    def density_ranking(self, level: Optional[CongestionLevel] = None, reported_only: bool = False,
                        limit: int = 50) -> list[BusDensity]:
        """Most crowded first (hybrid score); buses with no estimate yet come last."""
        ranked = sorted(self.bus_densities().values(),
                        key=lambda d: (d.score is None, -(d.score or 0), -d.report_count, d.bus_id))
        if level:
            ranked = [d for d in ranked if d.level is level]
        elif reported_only:
            ranked = [d for d in ranked if d.level is not None]
        return ranked[:limit]

    # ---------- Passenger reports ----------

    async def submit_report(self, passenger: Identity, payload: ReportIn) -> ReportReceipt:
        # Idempotency first: a retried submission returns the original report.
        existing = self.repo.find_submission(passenger.user_id, payload.submission_id)
        if existing:
            return ReportReceipt(report=existing, duplicate=True)

        bus = self.fleet.get(payload.bus_id)
        if bus is None:
            raise DomainError(f"Unknown bus '{payload.bus_id}'", 404)
        if payload.stop_id and payload.stop_id not in {s.id for s in self.network.route_detail(bus.route_id).stops}:
            raise DomainError(f"Stop '{payload.stop_id}' is not served by line {bus.route_number}", 422)

        now = self.clock.now()
        last = self.repo.last_report_time(passenger.user_id, bus.bus_id)
        cooldown = timedelta(seconds=self.settings.report_cooldown_seconds)
        if last and now - last < cooldown:
            wait = int((cooldown - (now - last)).total_seconds()) + 1
            raise DomainError(f"You already reported bus {bus.bus_id}. Try again in {wait}s.", 429)

        comment = payload.comment.strip() if payload.comment and payload.comment.strip() else None
        report, created = self.repo.insert_report_once(
            passenger.user_id, payload.submission_id, bus.bus_id, bus.route_id, payload.stop_id,
            payload.level, comment, now,
        )
        if created:
            await self.events.publish("report", report.model_dump(mode="json"), OPERATOR)
        return ReportReceipt(report=report, duplicate=not created)

    def my_reports(self, passenger: Identity) -> list[Report]:
        return self.repo.list_reports(passenger_id=passenger.user_id, limit=50)

    async def update_report(self, report_id: int, payload: ReportUpdateIn, operator: Identity) -> Report:
        report = self.repo.get_report(report_id)
        if report is None:
            raise DomainError(f"Report {report_id} not found", 404)
        if payload.status not in REPORT_TRANSITIONS[report.status]:
            raise DomainError(f"Cannot move report from {report.status.value} to {payload.status.value}", 409)
        updated = self.repo.update_report(report_id, payload.status, operator.email, payload.note, self.clock.now())
        await self.events.publish("report", updated.model_dump(mode="json"), OPERATOR)
        return updated

    # ---------- Alerts ----------

    async def set_alert_status(self, alert_id: int, status: AlertStatus, operator: Identity) -> Alert:
        alert = self.repo.get_alert(alert_id)
        if alert is None:
            raise DomainError(f"Alert {alert_id} not found", 404)
        allowed = {AlertStatus.ACKNOWLEDGED: {AlertStatus.ACTIVE},
                   AlertStatus.RESOLVED: {AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED}}
        if alert.status not in allowed.get(status, set()):
            raise DomainError(f"Alert {alert_id} is already {alert.status.value}", 409)
        updated = self.repo.set_alert_status(alert_id, status, operator.email, self.clock.now())
        await self._publish_alert(updated)
        return updated

    def live_payload(self) -> dict:
        """Bus positions + per-bus and per-line density, computed once per tick for all clients."""
        densities = self.bus_densities()
        buses = [self._position(b, densities.get(b.bus_id)).model_dump(mode="json", exclude={"updated_at"})
                 for b in self.fleet.all()]
        routes = [r.model_dump(mode="json") for r in self.route_densities(densities)]
        return {"buses": buses, "routes": routes}

    def operator_snapshot(self) -> dict:
        return {
            **self.live_payload(),
            "reports": [r.model_dump(mode="json") for r in self.repo.list_reports(limit=100)],
            "alerts": [a.model_dump(mode="json") for a in self.repo.list_alerts(limit=50)],
        }

    # ---------- Background processes ----------

    async def _publish_alert(self, alert: Alert) -> None:
        await self.events.publish("alert", alert.model_dump(mode="json"), OPERATOR)

    async def _gps_loop(self) -> None:
        dt = self.settings.simulation_tick_seconds
        while True:
            self.fleet.tick(dt, self.clock.now())
            if self.events.subscriber_count:
                live = self.live_payload()
                await self.events.publish("buses", live["buses"], PUBLIC)
                await self.events.publish("routes", live["routes"], PUBLIC)
            await asyncio.sleep(dt)

    async def _anomaly_loop(self) -> None:
        """Cron-like job: samples hybrid density and re-evaluates the 10-minute rule."""
        while True:
            try:
                self.record_density_samples(self.clock.now())
                await self.monitor.run_once()
            except Exception:  # keep the scheduler alive no matter what
                log.exception("Anomaly scan failed")
            await asyncio.sleep(self.settings.anomaly_check_interval_seconds)

    def start_background_tasks(self) -> None:
        self._tasks.append(asyncio.create_task(self._gps_loop(), name="gps-simulator"))
        self._tasks.append(asyncio.create_task(self._anomaly_loop(), name="anomaly-monitor"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.repo.close()
