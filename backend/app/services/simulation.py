"""Mock GPS/AVL feed: buses drive the real line geometry and halt at every stop.

No live AVL feed is available for the prototype, so this module produces the
GPS telemetry a real fleet would send: positions, speeds and how long each bus
stood at each stop. Crowdedness is NOT simulated here - it is inferred from
this telemetry by ``services.telemetry`` and fused with passenger reports.

Dwell model: passengers accumulate at a stop between two buses of the same
line (more at hub stops and during rush hour); boarding time grows with the
number waiting. A late bus therefore waits longer at every stop and falls
further behind while the bus after it catches up - the classic bus-bunching
pattern operators see in real GPS traces.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.network import TransitNetwork

FREE_FLOW_KMH = 24.0          # cruising speed between stops
RUSH_HOUR_SPEED_FACTOR = 0.75  # heavier traffic at peak
DWELL_BASE_S = 12.0           # doors open / close, alighting
BOARDING_S_PER_PASSENGER = 2.5
MAX_DWELL_S = 120.0
ARRIVALS_PER_MIN = 0.08       # passengers per minute per (line, stop), before hub / peak factors
RUSH_HOUR_DEMAND_FACTOR = 2.2
BAKU_UTC_OFFSET = timedelta(hours=4)
RUSH_HOURS = ((7.5, 9.5), (17.5, 19.5))  # local time
LOOP_KM_PER_BUS = 20
TELEMETRY_STOPS = 6           # completed stops kept per bus for the GPS estimator


@dataclass
class SimBus:
    bus_id: str
    route_id: str
    route_number: str
    s: float                       # metres travelled along the route loop
    speed_kmh: float = 0.0         # current speed (0 while standing at a stop)
    moving_speed_kmh: float = FREE_FLOW_KMH  # smoothed speed while moving
    dwell_left_s: float = 0.0      # simulated seconds still to stand at the current stop
    dwell_planned_s: float = 0.0
    at_stop: Optional[str] = None
    dwells: deque = field(default_factory=lambda: deque(maxlen=TELEMETRY_STOPS))  # (time, stop_id, seconds)

    @property
    def dwelling(self) -> bool:
        return self.at_stop is not None


def is_rush_hour(now: datetime, mode: str = "auto") -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    local = now.astimezone(timezone.utc) + BAKU_UTC_OFFSET
    hour = local.hour + local.minute / 60
    return local.weekday() < 5 and any(start <= hour < end for start, end in RUSH_HOURS)


class BusFleet:
    def __init__(self, network: TransitNetwork, speed_multiplier: float = 1.0,
                 rng: Optional[random.Random] = None, rush_hour_mode: str = "auto") -> None:
        self._network = network
        self._multiplier = speed_multiplier
        self._rng = rng or random.Random()
        self._rush_mode = rush_hour_mode
        self._buses: dict[str, SimBus] = {}
        self._last_visit: dict[tuple[str, str], datetime] = {}
        self._hub_factor = {s.id: 1 + min(len(network.routes_serving(s.id)), 20) / 5 for s in network.stops}
        self.updated_at: Optional[datetime] = None
        counters: dict[str, int] = {}
        for route in network.routes:
            length = network.path(route.id).length_m
            count = max(2, min(4, round(length / 1000 / LOOP_KM_PER_BUS)))
            for i in range(count):
                counters[route.number] = counters.get(route.number, 0) + 1
                bus_id = f"{route.number}-{counters[route.number]:02d}"
                self._buses[bus_id] = SimBus(bus_id, route.id, route.number, s=length * i / count)

    # ---------- simulation ----------

    def rush_hour(self, now: datetime) -> bool:
        return is_rush_hour(now, self._rush_mode)

    def expected_headway_s(self, route_id: str) -> float:
        """Simulated seconds between two buses of a line at even spacing."""
        count = len(self.all(route_id)) or 1
        return self._network.path(route_id).length_m / count / (FREE_FLOW_KMH / 3.6)

    def tick(self, dt_seconds: float, now: datetime) -> None:
        dt_sim = dt_seconds * self._multiplier
        peak = self.rush_hour(now)
        for bus in self._buses.values():
            if bus.dwelling:
                bus.speed_kmh = 0.0
                bus.dwell_left_s -= dt_sim
                if bus.dwell_left_s <= 0:
                    self._depart(bus, now)
                continue

            target = FREE_FLOW_KMH * self._rng.uniform(0.75, 1.2) * (RUSH_HOUR_SPEED_FACTOR if peak else 1.0)
            bus.speed_kmh += (target - bus.speed_kmh) * 0.4  # accelerate from standstill / traffic
            bus.moving_speed_kmh = 0.85 * bus.moving_speed_kmh + 0.15 * bus.speed_kmh
            advance = bus.speed_kmh / 3.6 * dt_sim
            path = self._network.path(bus.route_id)
            nxt = path.next_pass(bus.s)
            if nxt and advance >= nxt[0]:
                bus.s = (bus.s + nxt[0]) % path.length_m  # stop exactly at the stop
                self._arrive(bus, nxt[1], now, peak)
            else:
                bus.s = (bus.s + advance) % path.length_m
        self.updated_at = now

    def _arrive(self, bus: SimBus, stop_id: str, now: datetime, peak: bool) -> None:
        key = (bus.route_id, stop_id)
        last = self._last_visit.get(key)
        waited_s = ((now - last).total_seconds() * self._multiplier if last
                    else self.expected_headway_s(bus.route_id))
        rate = ARRIVALS_PER_MIN * self._hub_factor.get(stop_id, 1.0) * (RUSH_HOUR_DEMAND_FACTOR if peak else 1.0)
        waiting = max(0.0, self._rng.gauss(rate * waited_s / 60, 1.0))
        dwell = min(MAX_DWELL_S, DWELL_BASE_S + BOARDING_S_PER_PASSENGER * waiting)
        self._last_visit[key] = now
        bus.at_stop, bus.speed_kmh = stop_id, 0.0
        bus.dwell_left_s = bus.dwell_planned_s = dwell

    def _depart(self, bus: SimBus, now: datetime) -> None:
        bus.dwells.append((now, bus.at_stop, bus.dwell_planned_s))  # observed stop duration (telemetry)
        bus.at_stop, bus.dwell_left_s = None, 0.0

    # ---------- queries ----------

    def get(self, bus_id: str) -> Optional[SimBus]:
        return self._buses.get(bus_id)

    def all(self, route_id: Optional[str] = None) -> list[SimBus]:
        return [b for b in self._buses.values() if route_id is None or b.route_id == route_id]

    def location(self, bus: SimBus) -> tuple[float, float, Optional[str]]:
        path = self._network.path(bus.route_id)
        lat, lng = path.position_at(bus.s)
        return lat, lng, bus.at_stop or path.next_stop(bus.s)

    def gap_ahead_ratio(self, bus: SimBus) -> Optional[float]:
        """Distance to the next bus of the same line, relative to even spacing (1.0 = on schedule)."""
        same = [b for b in self._buses.values() if b.route_id == bus.route_id]
        if len(same) < 2:
            return None
        length = self._network.path(bus.route_id).length_m
        gap = min((b.s - bus.s) % length for b in same if b is not bus) or length
        return gap / (length / len(same))

    def eta(self, bus: SimBus, stop_id: str) -> Optional[tuple[float, float]]:
        """(distance_m, eta_seconds): driving time + remaining dwell + expected dwell at stops on the way."""
        path = self._network.path(bus.route_id)
        if bus.at_stop == stop_id:
            return 0.0, 0.0
        distance = path.distance_to_stop(bus.s, stop_id)
        if distance is None:
            return None
        avg_dwell = (sum(d for *_, d in bus.dwells) / len(bus.dwells)) if bus.dwells else DWELL_BASE_S + 10
        driving = distance / (max(bus.moving_speed_kmh, 5.0) / 3.6)
        stops_on_way = path.passes_between(bus.s, distance)
        sim_seconds = driving + bus.dwell_left_s + stops_on_way * avg_dwell
        return distance, sim_seconds / self._multiplier

    def buses_for_stop(self, stop_id: str, route_id: Optional[str] = None) -> list[SimBus]:
        route_ids = {r.id for r in self._network.routes_serving(stop_id)}
        if route_id:
            route_ids &= {route_id}
        return [b for b in self._buses.values() if b.route_id in route_ids]
