"""Network, search, stop view, per-bus density analytics and the imported KMZ data."""
import json
from pathlib import Path

import pytest

from app.models import CongestionLevel
from app.services.density import DensityEstimator
from app.services.network import RoutePath, TransitNetwork, normalize

NETWORK_JSON = Path(__file__).resolve().parent.parent / "app" / "data" / "network.json"


def test_dual_search_finds_stops_and_bus_numbers(passenger):
    res = passenger.get("/api/search", params={"q": "88"}).json()
    assert [r["number"] for r in res["routes"]] == ["88", "88A"]  # exact match ranked first
    stops = passenger.get("/api/search", params={"q": "neriman"}).json()["stops"]
    assert [s["id"] for s in stops] == ["S3"]  # diacritic-insensitive
    assert passenger.get("/api/stops", params={"q": "KOROĞLU"}).json()[0]["id"] == "S5"


def test_stop_view_lists_every_line_and_bus_passing_through(passenger):
    stop = passenger.get("/api/stops/S2").json()
    assert {r["number"] for r in stop["routes"]} == {"1", "88"}
    arrivals = passenger.get("/api/stops/S2/arrivals").json()
    assert {a["route_number"] for a in arrivals} == {"1", "88"}
    assert [a["eta_seconds"] for a in arrivals] == sorted(a["eta_seconds"] for a in arrivals)
    assert all(a["level"] is None and a["report_count"] == 0 for a in arrivals), "no reports yet -> no data"
    assert passenger.get("/api/stops/NOPE").status_code == 404


def test_nearest_stop_for_current_location(passenger):
    nearest = passenger.get("/api/stops/nearest", params={"lat": 40.3720, "lng": 49.8440, "limit": 1}).json()
    assert nearest[0]["id"] == "S1"


def test_route_detail_and_buses(passenger):
    detail = passenger.get("/api/routes/L1").json()
    assert [s["id"] for s in detail["stops"]] == ["S1", "S2", "S3"] and len(detail["geometry"]) >= 2
    buses = passenger.get("/api/buses", params={"route_id": "L1"}).json()
    assert len(buses) >= 2 and all(b["route_number"] == "1" for b in buses)
    assert passenger.get("/api/buses/zzz").status_code == 404


def test_out_and_back_path_passes_each_stop_twice():
    path = RoutePath([(40.0, 49.0), (40.0, 49.01), (40.0, 49.02)], [("A", 0), ("B", 853), ("C", 1706)], closed=False)
    assert path.length_m == pytest.approx(2 * 1706, rel=0.01)
    assert path.distance_to_stop(900, "B") == pytest.approx(2 * 1706 - 853 - 900, abs=5)
    assert path.next_stop(900) == "C"


def test_report_estimator_is_recency_weighted(clock, add_reports, engine):
    est = DensityEstimator(window_minutes=15)
    assert est.estimate([], clock.now()).score is None
    add_reports("1-01", [(0, "HIGH"), (1, "HIGH"), (8, "MEDIUM")])  # three passengers
    reports = engine.repo.reports_since(clock.now().replace(year=2000))
    result = est.estimate(reports, clock.now())
    assert result.consensus and result.report_count == 3
    assert 2.5 < result.score < 3, "the older MEDIUM vote counts less than the fresh HIGH votes"


def test_operator_density_ranks_reported_buses_first(operator, add_reports):
    add_reports("88-02", [(0, "HIGH"), (0.5, "HIGH"), (1, "HIGH")])
    add_reports("1-01", [(0, "LOW"), (0.5, "LOW"), (1, "LOW")])
    ranking = operator.get("/api/operator/density", params={"limit": 1000}).json()
    assert [d["bus_id"] for d in ranking[:2]] == ["88-02", "1-01"]
    assert all(d["level"] is None for d in ranking[2:])
    reported = operator.get("/api/operator/density", params={"reported_only": True}).json()
    assert [d["bus_id"] for d in reported] == ["88-02", "1-01"]
    assert [d["bus_id"] for d in operator.get("/api/operator/density", params={"level": "HIGH"}).json()] == ["88-02"]


def test_alert_acknowledge_and_resolve_by_hand(operator, engine, clock, add_reports):
    # passengers report bus 1-02 as HIGH every 30 s while the job samples; the HIGH status (and the
    # 10-minute streak) only starts once the third passenger agrees, hence 12 minutes of reports
    for _ in range(24):
        add_reports("1-02", [(0, "HIGH")])
        engine.record_density_samples(clock.now())
        clock.advance(seconds=30)
    add_reports("1-02", [(0, "HIGH")])
    alert = operator.post("/api/operator/anomaly/run").json()[0]
    assert alert["bus_id"] == "1-02" and "Additional bus reserve must be allocated" in alert["message"]
    assert "bus 2 " in alert["message"]
    assert operator.post(f"/api/operator/alerts/{alert['id']}/acknowledge").json()["status"] == "ACKNOWLEDGED"
    resolved = operator.post(f"/api/operator/alerts/{alert['id']}/resolve").json()
    assert resolved["status"] == "RESOLVED" and resolved["handled_by"] == "rashad@example.com"
    assert operator.post(f"/api/operator/alerts/{alert['id']}/resolve").status_code == 409


def test_normalize_folds_azerbaijani_letters():
    assert normalize("Əhmədli Şüvəlan Çıraq Göy İçərişəhər") == "ehmedli suvelan ciraq goy iceriseher"


@pytest.mark.skipif(not NETWORK_JSON.exists(), reason="network.json not generated")
def test_imported_kmz_network_integrity():
    data = json.loads(NETWORK_JSON.read_text("utf-8"))
    network = TransitNetwork.from_dict(data)
    assert len(network.routes) == 243 and len({r.number for r in network.routes}) == 239
    assert len(network.stops) > 1000
    stop_ids = {s.id for s in network.stops}
    for raw in data["routes"]:
        assert all(sid in stop_ids for sid, _ in raw["stops"]), raw["id"]
        assert raw["length_m"] > 1000 and len(raw["geometry"]) >= 2
    assert sum(1 for r in network.routes if r.stop_count >= 2) >= 230
