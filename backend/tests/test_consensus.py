"""Weighted consensus: a single passenger report never dictates a bus's status."""
import pytest

from app.models import CongestionLevel
from app.services.density import DensityEstimator, fuse


@pytest.fixture
def riders(new_client):
    """riders(n) -> n signed-in passengers, each with their own session."""
    def _make(n, prefix="rider"):
        clients = []
        for i in range(n):
            c = new_client()
            assert c.post("/api/auth/register", json={"email": f"{prefix}{i}@example.com", "password": "password-123"}).status_code == 201
            clients.append(c)
        return clients
    return _make


def _report(client, report_payload, level, bus_id="1-01"):
    assert client.post("/api/passenger/reports", json=report_payload(bus_id=bus_id, level=level)).status_code == 201


def test_a_single_high_report_does_not_change_the_status(riders, engine, report_payload):
    (rider,) = riders(1)
    _report(rider, report_payload, "HIGH")
    d = engine.bus_densities()["1-01"]
    assert d.level is None and d.report_count == 1 and not d.consensus_reached, "one report is only pending"
    assert rider.get("/api/buses/1-01").json()["level"] is None


def test_a_single_high_report_does_not_override_gps(riders, engine, clock, report_payload):
    bus = engine.fleet.get("1-01")
    for _ in range(3):
        bus.dwells.append((clock.now(), "S2", 20.0))  # normal stops -> GPS says LOW
    (rider,) = riders(1)
    _report(rider, report_payload, "HIGH")
    d = engine.bus_densities()["1-01"]
    assert (d.level, d.source) == (CongestionLevel.LOW, "gps")


def test_status_changes_once_enough_different_passengers_agree(riders, engine, report_payload):
    clients = riders(3)
    _report(clients[0], report_payload, "HIGH")
    _report(clients[1], report_payload, "HIGH")
    d = engine.bus_densities()["1-01"]
    assert d.level is None and (d.reporters, d.reporters_needed) == (2, 3)

    _report(clients[2], report_payload, "HIGH")
    d = engine.bus_densities()["1-01"]
    assert d.consensus_reached and (d.level, d.source, d.reporters) == (CongestionLevel.HIGH, "reports", 3)


def test_repeated_reports_from_one_passenger_count_as_one_vote(riders, engine, clock, report_payload):
    (rider,) = riders(1)
    for _ in range(4):  # the same person reports every minute (after the cooldown)
        _report(rider, report_payload, "HIGH")
        clock.advance(seconds=61)
    d = engine.bus_densities()["1-01"]
    assert (d.report_count, d.reporters, d.consensus_reached, d.level) == (4, 1, False, None)


def test_a_lone_outlier_is_ignored(add_reports, engine):
    add_reports("1-01", [(0, "LOW"), (0.5, "LOW"), (1, "LOW"), (1.5, "HIGH")])
    d = engine.bus_densities()["1-01"]
    assert d.level is CongestionLevel.LOW and d.outlier_reports == 1
    assert d.report_score == pytest.approx(1.0)


def test_a_substantial_minority_is_not_discarded(add_reports, engine):
    # 3 LOW vs 2 HIGH: 40 % of the votes is a real signal, not an outlier -> MEDIUM
    add_reports("1-01", [(0, "LOW"), (0, "HIGH"), (0, "LOW"), (0, "HIGH"), (0, "LOW")])
    d = engine.bus_densities()["1-01"]
    assert d.level is CongestionLevel.MEDIUM and d.outlier_reports == 0


def test_old_reports_do_not_reach_the_threshold(add_reports, engine):
    add_reports("1-01", [(12, "HIGH"), (12, "HIGH"), (13, "HIGH")])  # three voters, but stale
    d = engine.bus_densities()["1-01"]
    assert d.reporters == 3 and not d.consensus_reached and d.level is None


def test_density_rises_gradually_as_more_passengers_agree(add_reports, engine, clock):
    bus = engine.fleet.get("1-01")
    for _ in range(3):
        bus.dwells.append((clock.now(), "S2", 20.0))  # GPS: LOW
    scores = []
    for n in range(1, 6):
        add_reports("1-01", [(0, "HIGH")], reporter=n - 1)
        scores.append(engine.bus_densities()["1-01"].score)
    gps = engine.bus_densities()["1-01"].gps_score
    assert scores[0] == scores[1] == gps, "1-2 reports: still pending, GPS decides"
    assert gps < scores[2] < scores[3] <= scores[4], "influence grows with every agreeing passenger"
    assert CongestionLevel.from_score(scores[2]) is CongestionLevel.MEDIUM
    assert CongestionLevel.from_score(scores[4]) is CongestionLevel.HIGH, "a broad consensus reaches HIGH"


def test_estimator_thresholds_are_configurable(clock):
    class R:
        def __init__(self, email, level):
            self.passenger_email, self.level, self.created_at = email, CongestionLevel(level), clock.now()

    strict = DensityEstimator(window_minutes=15, min_reporters=5)
    assert not strict.estimate([R(f"p{i}", "HIGH") for i in range(4)], clock.now()).consensus
    assert strict.estimate([R(f"p{i}", "HIGH") for i in range(5)], clock.now()).consensus


def test_fusion_share_scales_with_confidence():
    assert fuse(3.0, 1.0, report_confidence=1.0).score == pytest.approx(2.7)
    assert fuse(3.0, 1.0, report_confidence=0.5).score == pytest.approx(1.85)
    assert fuse(None, 1.0, report_confidence=0.0).source == "gps"
