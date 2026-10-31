"""The 10-minute continuous-HIGH rule over hybrid density samples, with manual-only alert closing."""
import asyncio
from datetime import timedelta

import pytest

from app.models import AlertStatus, Identity, Role
from app.services.anomaly import ALERT_MESSAGE, ContinuousHighDetector


@pytest.fixture
def detector():
    return ContinuousHighDetector(window_minutes=10, bucket_minutes=2, high_ratio=0.6)


def every_half_minute(minutes, level="HIGH", start=0.0):
    """Reports every 30 s from ``start`` to ``start + minutes`` minutes ago."""
    return [(start + i * 0.5, level) for i in range(int(minutes * 2) + 1)]


def test_sustained_high_for_10_minutes_triggers_anomaly(detector, clock, make_obs):
    result = detector.evaluate(make_obs(every_half_minute(10.5)), clock.now())
    assert result.is_anomaly is True
    assert result.high_since == clock.now() - timedelta(minutes=10.5)


def test_high_for_less_than_10_minutes_is_not_an_anomaly(detector, clock, make_obs):
    result = detector.evaluate(make_obs(every_half_minute(8)), clock.now())
    assert result.is_anomaly is False
    assert result.high_streak_minutes == pytest.approx(8, abs=0.01)


def test_gap_without_reports_breaks_continuity(detector, clock, make_obs):
    obs = make_obs(every_half_minute(5, start=7) + every_half_minute(3))  # 4-minute hole
    result = detector.evaluate(obs, clock.now())
    assert result.is_anomaly is False and result.high_streak_minutes <= 4


def test_medium_period_resets_the_streak(detector, clock, make_obs):
    spec = [(m, "MEDIUM" if 4 < m <= 6 else lvl) for m, lvl in every_half_minute(12)]
    assert detector.evaluate(make_obs(spec), clock.now()).is_anomaly is False


def test_minority_noise_does_not_block_the_alert(detector, clock, make_obs):
    spec = [(m, "LOW" if i % 4 == 3 else "HIGH") for i, (m, _) in enumerate(every_half_minute(11))]
    assert detector.evaluate(make_obs(spec), clock.now()).is_anomaly is True


def test_stale_high_period_does_not_trigger(detector, clock, make_obs):
    result = detector.evaluate(make_obs(every_half_minute(15, start=5)), clock.now())
    assert result.is_anomaly is False and result.high_since is None


@pytest.mark.parametrize("kwargs", [{"window_minutes": 0}, {"bucket_minutes": -1}, {"high_ratio": 0}, {"high_ratio": 1.5}])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        ContinuousHighDetector(**kwargs)


def _report_and_sample(engine, clock, add_reports, buses, minutes, level="HIGH"):
    """Passengers report ``buses`` every 30 s while the density job samples."""
    for _ in range(int(minutes * 2) + 1):
        for bus_id in buses:
            add_reports(bus_id, [(0, level)])
        engine.record_density_samples(clock.now())
        clock.advance(seconds=30)
    clock.advance(seconds=-30)


def test_off_peak_gps_traffic_alone_raises_no_alerts(engine, clock):
    """Normal GPS behaviour (no rush hour, no reports) must not trigger the overcrowding rule."""
    for _ in range(40):
        clock.advance(seconds=30)
        engine.fleet.tick(30, clock.now())
        engine.record_density_samples(clock.now())
        assert asyncio.run(engine.monitor.run_once()) == []


def test_reported_overcrowding_raises_one_alert_per_route_that_never_auto_closes(engine, clock, add_reports):
    _report_and_sample(engine, clock, add_reports, ["88-01", "88-02"], 11)  # two buses, same line

    created = asyncio.run(engine.monitor.run_once())
    assert len(created) == 1, "one open alert per line, not per bus"
    alert = created[0]
    assert alert.route_id == "L88" and alert.bus_id in {"88-01", "88-02"}
    assert ALERT_MESSAGE in alert.message and alert.status is AlertStatus.ACTIVE
    assert "-0" not in alert.message, "bus numbers are shown formatted (88-02 -> bus 2)"

    # passengers now report LOW and time passes: the alert must stay open
    for _ in range(30):
        clock.advance(seconds=30)
        add_reports("88-01", [(0, "LOW")])
        engine.record_density_samples(clock.now())
        assert asyncio.run(engine.monitor.run_once()) == []
    assert engine.repo.get_alert(alert.id).status is AlertStatus.ACTIVE


def test_after_manual_resolution_a_fresh_10_minute_streak_is_required(engine, clock, add_reports):
    admin = Identity(user_id=1, email="admin@transit.az", role=Role.ADMIN)
    _report_and_sample(engine, clock, add_reports, ["1-01"], 11)
    alert = asyncio.run(engine.monitor.run_once())[0]
    asyncio.run(engine.set_alert_status(alert.id, AlertStatus.RESOLVED, admin))

    clock.advance(seconds=30)
    _report_and_sample(engine, clock, add_reports, ["1-01"], 5)  # still HIGH, but only since the resolution
    assert asyncio.run(engine.monitor.run_once()) == []

    clock.advance(seconds=30)
    _report_and_sample(engine, clock, add_reports, ["1-01"], 6)
    again = asyncio.run(engine.monitor.run_once())
    assert len(again) == 1 and again[0].id != alert.id


def test_end_to_end_reports_via_api_raise_alert(new_client, operator, engine, clock):
    """Different passengers report bus 88A-01 as HIGH through the API for 12 minutes.

    The first two reports are only pending; the HIGH status - and the 10-minute streak - starts
    when the third passenger agrees.
    """
    for i in range(25):
        c = new_client()
        c.post("/api/auth/register", json={"email": f"p{i}@example.com", "password": "password-123"})
        res = c.post("/api/passenger/reports", json={"bus_id": "88A-01", "level": "HIGH", "submission_id": f"sub-{i:08d}"})
        assert res.status_code == 201
        engine.record_density_samples(clock.now())
        if i == 1:
            assert asyncio.run(engine.monitor.run_once()) == [] and engine.bus_densities()["88A-01"].level is None
        if i < 24:
            clock.advance(seconds=30)
    created = operator.post("/api/operator/anomaly/run").json()
    assert [a["bus_id"] for a in created] == ["88A-01"]
    assert operator.get("/api/operator/density", params={"reported_only": True}).json()[0]["high_streak_minutes"] >= 10
