"""Stop-halting simulation, GPS-driven crowd estimation, hybrid fusion, line density, formatting."""
import random
from datetime import datetime, timezone

import pytest

from app.models import CongestionLevel, bus_index
from app.services.density import fuse
from app.services.network import RoutePath
from app.services.simulation import DWELL_BASE_S, MAX_DWELL_S, BusFleet, is_rush_hour
from app.services.telemetry import BusTelemetry, gps_crowd_score
from tests.conftest import BY_ID


def _drive_until_stop(fleet, bus, clock, max_ticks=2000):
    for _ in range(max_ticks):
        clock.advance(seconds=1)
        fleet.tick(1, clock.now())
        if bus.dwelling:
            return
    raise AssertionError("bus never reached a stop")


def test_bus_halts_exactly_at_the_stop_then_resumes(engine, clock):
    fleet, bus = engine.fleet, engine.fleet.get("1-01")
    bus.s = 50.0  # just after the first stop (S1) on line 1
    _drive_until_stop(fleet, bus, clock)

    assert bus.at_stop == "S2" and bus.speed_kmh == 0.0
    lat, lng, _ = fleet.location(bus)
    assert (round(lat, 4), round(lng, 4)) == (round(BY_ID["S2"]["lat"], 4), round(BY_ID["S2"]["lng"], 4))
    assert DWELL_BASE_S <= bus.dwell_planned_s <= MAX_DWELL_S

    standing_at = bus.s
    for _ in range(int(bus.dwell_planned_s) - 1):  # it stays put for the whole dwell
        clock.advance(seconds=1)
        fleet.tick(1, clock.now())
        assert bus.s == standing_at and bus.dwelling
    for _ in range(5):
        clock.advance(seconds=1)
        fleet.tick(1, clock.now())
    assert not bus.dwelling and bus.s > standing_at, "resumes after the dwell"
    assert bus.dwells[-1][1] == "S2", "the stop duration is recorded as telemetry"


def test_dwell_grows_with_waiting_passengers_and_rush_hour(engine):
    from app.services.network import TransitNetwork
    from tests.conftest import NETWORK

    def dwell_after(wait_minutes, mode):
        fleet = BusFleet(TransitNetwork.from_dict(NETWORK), rng=random.Random(3), rush_hour_mode=mode)
        bus = fleet.get("88-01")
        now = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
        fleet._last_visit[(bus.route_id, "S2")] = now.replace(minute=0) if wait_minutes else now
        fleet._arrive(bus, "S2", now.replace(minute=wait_minutes) if wait_minutes else now, fleet.rush_hour(now))
        return bus.dwell_planned_s

    assert dwell_after(40, "never") > dwell_after(0, "never"), "longer since the last bus -> more boarding"
    assert dwell_after(40, "always") > dwell_after(40, "never"), "rush hour -> more passengers"


def test_rush_hour_follows_baku_local_time():
    utc = timezone.utc
    assert is_rush_hour(datetime(2026, 10, 30, 4, 30, tzinfo=utc))       # Fri 08:30 Baku
    assert not is_rush_hour(datetime(2026, 10, 30, 8, 0, tzinfo=utc))    # Fri 12:00 Baku
    assert not is_rush_hour(datetime(2026, 10, 31, 4, 30, tzinfo=utc))   # Saturday
    assert is_rush_hour(datetime(2026, 10, 30, 8, 0, tzinfo=utc), "always")


def test_eta_includes_dwell_time(engine):
    fleet, bus = engine.fleet, engine.fleet.get("1-01")
    bus.s, bus.moving_speed_kmh = 0.0, 20.0
    distance, eta = fleet.eta(bus, "S3")
    driving_only = distance / (20 / 3.6)
    assert eta > driving_only, "a stop on the way (S2) adds expected dwell time"


def test_route_path_next_pass_and_passes_between():
    path = RoutePath([(40.0, 49.0), (40.0, 49.01), (40.0, 49.02)], [("A", 0), ("B", 853), ("C", 1706)], closed=False)
    assert path.next_pass(0)[1] == "B" and path.next_pass(853)[1] == "C"
    assert path.passes_between(0, 900) == 1 and path.passes_between(10, 800) == 0
    assert path.next_pass(path.length_m - 10)[1] == "A", "wraps around the loop"


@pytest.mark.parametrize("telemetry,expected", [
    (BusTelemetry(30, 24, 1.0, 4), CongestionLevel.LOW),     # normal stops, free flow, on schedule
    (BusTelemetry(75, 14, 1.1, 4), CongestionLevel.MEDIUM),  # long stops + slow
    (BusTelemetry(90, 12, 1.9, 4), CongestionLevel.HIGH),    # long stops, slow, big gap ahead (bunching)
])
def test_gps_crowd_score_reads_dwell_speed_and_clustering(telemetry, expected):
    assert CongestionLevel.from_score(gps_crowd_score(telemetry)) is expected


def test_gps_crowd_score_needs_enough_observations():
    assert gps_crowd_score(BusTelemetry(90, 10, 2.0, 1)) is None
    assert gps_crowd_score(BusTelemetry(None, 10, 2.0, 0)) is None
    # a bus bunched right behind another (small gap) is never read as more crowded than an on-time one
    on_time, bunched = BusTelemetry(40, 20, 1.0, 4), BusTelemetry(40, 20, 0.2, 4)
    assert gps_crowd_score(bunched) <= gps_crowd_score(on_time)


def test_fuse_weights_reports_above_gps():
    assert fuse(None, None).level is None
    assert fuse(3.0, None).source == "reports" and fuse(None, 1.0).source == "gps"
    both = fuse(3.0, 1.0)  # full-confidence passenger consensus vs. normal GPS behaviour
    assert both.source == "reports+gps" and both.score == pytest.approx(2.7) and both.level is CongestionLevel.HIGH


def test_line_density_aggregates_its_buses(passenger, engine, add_reports):
    add_reports("88-01", [(0, "HIGH"), (0.5, "HIGH"), (1, "HIGH")])
    add_reports("88-02", [(0, "MEDIUM"), (0.5, "MEDIUM"), (1, "MEDIUM")])
    lines = {r["route_id"]: r for r in passenger.get("/api/routes/density").json()}
    assert lines["L88"]["level"] in {"MEDIUM", "HIGH"} and lines["L88"]["high_bus_count"] == 1
    assert lines["L88"]["report_count"] == 6 and lines["L88"]["bus_count"] == 2
    assert lines["L1"]["level"] is None, "no telemetry and no reports yet"
    assert list(lines)[0] == "L88", "most crowded line first"


@pytest.mark.parametrize("bus_id,expected", [("88-02", "2"), ("M7A-10", "10"), ("5-00", "0"), ("174-03", "3")])
def test_bus_index_strips_leading_dashes_and_zeros(bus_id, expected):
    assert bus_index(bus_id) == expected
