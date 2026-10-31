"""Read-only network & live data shared by both interfaces (any signed-in user)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_engine, require_signed_in
from app.engine import TransitEngine
from app.models import (
    Arrival, BusPosition, Route, RouteDensity, RouteDetail, RouteLine, SearchResult, Stop, StopDetail,
)

router = APIRouter(prefix="/api", tags=["network"], dependencies=[Depends(require_signed_in)])


@router.get("/search", response_model=SearchResult)
def search(q: str = Query(..., min_length=1, max_length=64), limit: int = Query(10, ge=1, le=50),
           engine: TransitEngine = Depends(get_engine)):
    """Dual search: bus stops by name and bus lines by number."""
    return SearchResult(stops=engine.network.search_stops(q, limit), routes=engine.network.search_routes(q, limit))


@router.get("/routes", response_model=list[Route])
def list_routes(q: Optional[str] = Query(None, max_length=32), engine: TransitEngine = Depends(get_engine)):
    if q:
        return engine.network.search_routes(q, limit=50)
    return sorted(engine.network.routes, key=lambda r: (len(r.number), r.number, r.id))


@router.get("/routes/lines", response_model=list[RouteLine])
def route_lines(engine: TransitEngine = Depends(get_engine)):
    """Simplified geometry of every line (network overview map)."""
    return engine.network.lines()


@router.get("/routes/density", response_model=list[RouteDensity])
def route_density(engine: TransitEngine = Depends(get_engine)):
    """Crowding of every line, aggregated from its buses (passenger reports + GPS telemetry)."""
    return sorted(engine.route_densities(), key=lambda r: (r.score is None, -(r.score or 0), r.route_number))


@router.get("/routes/{route_id}", response_model=RouteDetail)
def get_route(route_id: str, engine: TransitEngine = Depends(get_engine)):
    detail = engine.network.route_detail(route_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown route '{route_id}'")
    return detail


@router.get("/stops", response_model=list[Stop])
def list_stops(q: Optional[str] = Query(None, max_length=64), limit: int = Query(20, ge=1, le=100),
               engine: TransitEngine = Depends(get_engine)):
    """All stops (for the map), or a name search when ``q`` is given."""
    return engine.network.search_stops(q, limit) if q else engine.network.stops


@router.get("/stops/nearest", response_model=list[Stop])
def nearest_stops(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
                  limit: int = Query(5, ge=1, le=20), engine: TransitEngine = Depends(get_engine)):
    return [s for s, _ in engine.network.nearest_stops(lat, lng, limit)]


@router.get("/stops/{stop_id}", response_model=StopDetail)
def get_stop(stop_id: str, engine: TransitEngine = Depends(get_engine)):
    """A stop with every bus line passing through it."""
    detail = engine.network.stop_detail(stop_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown stop '{stop_id}'")
    return detail


@router.get("/stops/{stop_id}/arrivals", response_model=list[Arrival])
def stop_arrivals(stop_id: str, route_id: Optional[str] = Query(None), limit: int = Query(20, ge=1, le=50),
                  engine: TransitEngine = Depends(get_engine)):
    """Every bus heading to this stop (all lines), soonest first, with its crowdedness."""
    return engine.arrivals(stop_id, route_id, limit)


@router.get("/buses", response_model=list[BusPosition])
def list_buses(route_id: Optional[str] = Query(None), engine: TransitEngine = Depends(get_engine)):
    if route_id and engine.network.route(route_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown route '{route_id}'")
    return engine.positions(route_id)


@router.get("/buses/{bus_id}", response_model=BusPosition)
def get_bus(bus_id: str, engine: TransitEngine = Depends(get_engine)):
    return engine.bus(bus_id)
