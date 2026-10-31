import random
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.engine import TransitEngine
from app.main import create_app
from app.models import CongestionLevel
from app.services.anomaly import Observation
from app.services.clock import FrozenClock
from app.services.mailer import ConsoleMailer
from app.services.network import TransitNetwork, haversine_m

ADMIN_EMAIL, ADMIN_PASS = "admin@transit.az", "test-admin-pass"
PASSENGER_EMAIL, PASSENGER_PASS = "aysel@example.com", "passenger-pass-1"
OPERATOR_EMAIL, OPERATOR_PASS = "rashad@example.com", "operator-pass-1"


def _line(route_id, number, stops, closed):
    """Route dict in network.json format with offsets computed from the stop coordinates."""
    geometry = [(s["lat"], s["lng"]) for s in stops] + ([(stops[0]["lat"], stops[0]["lng"])] if closed else [])
    offsets, total = [], 0.0
    for i, s in enumerate(stops):
        if i:
            p = stops[i - 1]
            total += haversine_m(p["lat"], p["lng"], s["lat"], s["lng"])
        offsets.append([s["id"], round(total, 1)])
    length = total + (haversine_m(stops[-1]["lat"], stops[-1]["lng"], stops[0]["lat"], stops[0]["lng"]) if closed else 0)
    return {"id": route_id, "number": number, "name": f"{stops[0]['name']} – {stops[-1]['name']}",
            "color": "#2563eb", "closed": closed, "geometry": geometry, "stops": offsets, "length_m": length}


STOPS = [
    {"id": "S1", "name": "Sahil", "lat": 40.3717, "lng": 49.8443},
    {"id": "S2", "name": "28 May", "lat": 40.3797, "lng": 49.8486},
    {"id": "S3", "name": "Nəriman Nərimanov", "lat": 40.4029, "lng": 49.8706},
    {"id": "S4", "name": "Gənclik", "lat": 40.4003, "lng": 49.8516},
    {"id": "S5", "name": "Koroğlu", "lat": 40.4180, "lng": 49.9184},
]
BY_ID = {s["id"]: s for s in STOPS}
NETWORK = {
    "stops": STOPS,
    "routes": [
        _line("L1", "1", [BY_ID[i] for i in ("S1", "S2", "S3")], closed=False),
        _line("L88", "88", [BY_ID[i] for i in ("S4", "S2", "S5")], closed=True),
        _line("L88A", "88A", [BY_ID[i] for i in ("S5", "S3")], closed=False),
    ],
}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_path=":memory:", admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASS, smtp_host=None,
        enable_background_tasks=False, frontend_dir="__no_frontend__", report_cooldown_seconds=60,
        simulation_speed_multiplier=1, anomaly_check_interval_seconds=30, rush_hour_mode="never",
    )


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def mailer() -> ConsoleMailer:
    return ConsoleMailer()


@pytest.fixture
def engine(settings, clock, mailer) -> TransitEngine:
    return TransitEngine(settings, network=TransitNetwork.from_dict(NETWORK), clock=clock,
                         rng=random.Random(7), mailer=mailer)


@pytest.fixture
def new_client(settings, engine):
    """Factory for extra clients - each has its own cookie jar (= its own browser)."""
    clients = []

    def _make() -> TestClient:
        c = TestClient(create_app(settings, engine))
        c.__enter__()
        clients.append(c)
        return c
    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(new_client):
    return new_client()


def login(c, email, password):
    res = c.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def passenger(new_client):
    """Client signed in as a self-registered passenger."""
    c = new_client()
    assert c.post("/api/auth/register", json={"email": PASSENGER_EMAIL, "password": PASSENGER_PASS}).status_code == 201
    return c


@pytest.fixture
def admin(new_client):
    c = new_client()
    login(c, ADMIN_EMAIL, ADMIN_PASS)
    return c


@pytest.fixture
def operator(new_client, admin):
    """A user who registered as passenger and was promoted to operator by the master admin."""
    c = new_client()
    user = c.post("/api/auth/register", json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASS}).json()
    assert admin.patch(f"/api/admin/users/{user['user_id']}", json={"role": "operator"}).status_code == 200
    login(c, OPERATOR_EMAIL, OPERATOR_PASS)
    return c


@pytest.fixture
def report_payload():
    def _make(bus_id="1-01", level="HIGH", **extra):
        return {"bus_id": bus_id, "level": level, "submission_id": str(uuid.uuid4()), **extra}
    return _make


@pytest.fixture
def add_reports(engine):
    """Insert passenger reports directly: add_reports("88-01", [(minutes_ago, "HIGH"), ...]).

    Reports rotate over five different passengers (consensus needs several voters);
    pass ``reporter=i`` to make one specific passenger report.
    """
    from app.models import Role
    now = engine.clock.now()
    user_ids = [engine.repo.create_user(f"reporter{i}@example.com", "x", Role.PASSENGER, now) for i in range(5)]
    turn = {"next": 0}

    def _add(bus_id, spec, at=None, reporter=None):
        bus = engine.fleet.get(bus_id)
        base = at or engine.clock.now()
        for minutes_ago, level in spec:
            if reporter is None:
                user_id = user_ids[turn["next"] % len(user_ids)]
                turn["next"] += 1
            else:
                user_id = user_ids[reporter]
            engine.repo.insert_report_once(user_id, str(uuid.uuid4()), bus_id, bus.route_id, None,
                                           CongestionLevel(level), None, base - timedelta(minutes=minutes_ago))
    return _add


@pytest.fixture
def make_obs(clock):
    """make_obs([(minutes_ago, "HIGH"), ...]) -> observations."""
    def _make(spec):
        return [Observation(clock.now() - timedelta(minutes=m), CongestionLevel(level)) for m, level in spec]
    return _make
