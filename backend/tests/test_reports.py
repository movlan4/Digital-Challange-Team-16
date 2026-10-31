"""Passenger reports: exactly-once storage, operator inbox, manual-only closing, report-driven density."""
import asyncio
import sqlite3

import pytest

from app.models import CongestionLevel
from app.services.events import OPERATOR, PUBLIC


def _row_count(engine) -> int:
    with engine.repo._lock:
        return engine.repo._conn.execute("SELECT COUNT(*) FROM bus_reports").fetchone()[0]


def test_submit_report_is_stored_once_and_visible_to_operator(passenger, operator, report_payload):
    res = passenger.post("/api/passenger/reports", json=report_payload(stop_id="S2", comment="  Packed  "))
    assert res.status_code == 201
    body = res.json()
    assert body["duplicate"] is False
    report = body["report"]
    assert report["bus_id"] == "1-01" and report["route_number"] == "1" and report["status"] == "OPEN"
    assert report["comment"] == "Packed" and report["passenger_email"] == "aysel@example.com"

    inbox = operator.get("/api/operator/reports", params={"status": "OPEN"}).json()
    assert [r["id"] for r in inbox] == [report["id"]]
    assert operator.get("/api/operator/reports/summary").json() == {"OPEN": 1, "IN_PROGRESS": 0, "RESOLVED": 0}


def test_resubmitting_the_same_submission_never_creates_a_duplicate(passenger, engine, report_payload):
    payload = report_payload()
    first = passenger.post("/api/passenger/reports", json=payload)
    retries = [passenger.post("/api/passenger/reports", json=payload) for _ in range(3)]

    assert first.status_code == 201
    assert all(r.status_code == 200 and r.json()["duplicate"] for r in retries)
    assert {r.json()["report"]["id"] for r in retries} == {first.json()["report"]["id"]}
    assert _row_count(engine) == 1
    assert engine.bus_densities()["1-01"].report_count == 1, "density counts it once"


def test_database_constraint_rejects_duplicate_submission_ids(passenger, engine):
    user_id = engine.repo.user_by_email("aysel@example.com")["id"]
    now = engine.clock.now()
    _, created = engine.repo.insert_report_once(user_id, "sub-12345", "1-01", "L1", None, CongestionLevel.LOW, None, now)
    _, again = engine.repo.insert_report_once(user_id, "sub-12345", "1-01", "L1", None, CongestionLevel.HIGH, None, now)
    assert (created, again) == (True, False)
    with engine.repo._lock, pytest.raises(sqlite3.IntegrityError):
        engine.repo._conn.execute(
            "INSERT INTO bus_reports (passenger_id, submission_id, bus_id, route_id, level, status, created_at, updated_at)"
            " VALUES (?, 'sub-12345', '1-01', 'L1', 'LOW', 'OPEN', 'x', 'x')", (user_id,))


def test_new_report_on_same_bus_is_rate_limited(passenger, clock, report_payload):
    assert passenger.post("/api/passenger/reports", json=report_payload()).status_code == 201
    assert passenger.post("/api/passenger/reports", json=report_payload()).status_code == 429
    assert passenger.post("/api/passenger/reports", json=report_payload(bus_id="88-01")).status_code == 201
    clock.advance(seconds=61)
    assert passenger.post("/api/passenger/reports", json=report_payload()).status_code == 201


def test_report_validation(passenger, report_payload):
    assert passenger.post("/api/passenger/reports", json=report_payload(bus_id="999-01")).status_code == 404
    assert passenger.post("/api/passenger/reports", json=report_payload(stop_id="S5")).status_code == 422
    assert passenger.post("/api/passenger/reports", json=report_payload(level="EXTREME")).status_code == 422
    bad = report_payload()
    bad["submission_id"] = "x"
    assert passenger.post("/api/passenger/reports", json=bad).status_code == 422


def test_reports_stay_open_until_an_operator_handles_them(passenger, operator, engine, clock, report_payload):
    report_id = passenger.post("/api/passenger/reports", json=report_payload()).json()["report"]["id"]

    for _ in range(12):  # hours pass and the background job runs: nothing closes the report
        clock.advance(minutes=30)
        asyncio.run(engine.monitor.run_once())
    assert engine.repo.get_report(report_id).status.value == "OPEN"

    res = operator.patch(f"/api/operator/reports/{report_id}", json={"status": "IN_PROGRESS"})
    assert res.status_code == 200 and res.json()["handled_by"] == "rashad@example.com"
    res = operator.patch(f"/api/operator/reports/{report_id}", json={"status": "RESOLVED", "note": "Extra bus sent"})
    assert res.json()["status"] == "RESOLVED" and res.json()["resolution_note"] == "Extra bus sent"
    assert operator.patch(f"/api/operator/reports/{report_id}", json={"status": "OPEN"}).status_code == 409
    assert operator.patch("/api/operator/reports/9999", json={"status": "RESOLVED"}).status_code == 404
    assert passenger.get("/api/passenger/reports").json()[0]["status"] == "RESOLVED"


def test_new_report_is_pushed_to_the_operator_stream_immediately(passenger, engine, report_payload):
    operator_queue = engine.events.subscribe(frozenset({PUBLIC, OPERATOR}))
    public_queue = engine.events.subscribe(frozenset({PUBLIC}))
    passenger.post("/api/passenger/reports", json=report_payload())
    message = operator_queue.get_nowait()
    assert message.startswith("event: report") and '"bus_id":"1-01"' in message
    assert public_queue.empty(), "reports are operator-only"


def test_density_fuses_passenger_consensus_with_gps_telemetry(new_client, engine, clock, report_payload):
    # before any telemetry or report there is no estimate - nothing is invented
    assert all(d.level is None for d in engine.bus_densities().values())

    # GPS telemetry: short, normal stops -> GPS alone estimates LOW
    bus = engine.fleet.get("1-01")
    for _ in range(3):
        bus.dwells.append((clock.now(), "S2", 20.0))
    assert (engine.bus_densities()["1-01"].source, engine.bus_densities()["1-01"].level) == ("gps", CongestionLevel.LOW)

    for i in range(3):  # three different passengers agree the bus is packed
        c = new_client()
        c.post("/api/auth/register", json={"email": f"rider{i}@example.com", "password": "password-123"})
        assert c.post("/api/passenger/reports", json=report_payload(level="HIGH")).status_code == 201
    d = engine.bus_densities()["1-01"]
    assert d.consensus_reached and d.source == "reports+gps"
    assert d.report_score > d.score > d.gps_score, "fused score lies between the two signals"
    assert engine.bus_densities()["1-02"].level is None, "other buses are unaffected"

    api_bus = c.get("/api/buses/1-01").json()
    assert (api_bus["density_source"], api_bus["report_count"]) == ("reports+gps", 3)

    clock.advance(minutes=16)  # reports expired -> GPS alone keeps estimating
    d = engine.bus_densities()["1-01"]
    assert (d.source, d.report_count) == ("gps", 0) and d.level is CongestionLevel.LOW
