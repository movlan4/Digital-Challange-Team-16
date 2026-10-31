"""Static transit network built from the imported KMZ data (app/data/network.json)."""
from __future__ import annotations

import bisect
import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.models import Route, RouteDetail, RouteLine, Stop, StopDetail, StopOnRoute

EARTH_RADIUS_M = 6_371_000
_AZ_FOLD = str.maketrans({"ə": "e", "Ə": "e", "ı": "i", "İ": "i", "ş": "s", "ç": "c", "ğ": "g", "ö": "o", "ü": "u"})


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def normalize(text: str) -> str:
    """Case- and diacritic-insensitive search key ('Nəriman' == 'neriman')."""
    text = text.translate(_AZ_FOLD).casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


class RoutePath:
    """The loop a bus drives: the line itself if circular, otherwise out-and-back.

    ``stop_offsets`` are distances (m) of each stop along the one-way line;
    on an out-and-back loop every stop is passed twice (s and 2L - s).
    """

    def __init__(self, geometry: list[tuple[float, float]], stop_offsets: list[tuple[str, float]], closed: bool):
        points = list(geometry) if closed else list(geometry) + list(reversed(geometry[:-1]))
        if closed and points[0] != points[-1]:
            points.append(points[0])
        self.points = points
        self.cumulative = [0.0]
        for a, b in zip(points, points[1:]):
            self.cumulative.append(self.cumulative[-1] + haversine_m(a[0], a[1], b[0], b[1]))
        self.length_m = self.cumulative[-1] or 1.0
        one_way = self.cumulative[len(geometry) - 1]

        passes = []
        for stop_id, offset in stop_offsets:
            passes.append((offset, stop_id))
            if not closed:
                passes.append((2 * one_way - offset, stop_id))
        passes.sort()
        self._pass_offsets = [p[0] for p in passes]
        self._pass_stops = [p[1] for p in passes]
        self._passes_by_stop: dict[str, list[float]] = {}
        for offset, stop_id in passes:
            self._passes_by_stop.setdefault(stop_id, []).append(offset)

    def position_at(self, s: float) -> tuple[float, float]:
        s %= self.length_m
        i = max(0, min(bisect.bisect_right(self.cumulative, s) - 1, len(self.points) - 2))
        seg = self.cumulative[i + 1] - self.cumulative[i]
        t = 0.0 if seg == 0 else (s - self.cumulative[i]) / seg
        (a1, b1), (a2, b2) = self.points[i], self.points[i + 1]
        return a1 + (a2 - a1) * t, b1 + (b2 - b1) * t

    def next_stop(self, s: float) -> Optional[str]:
        nxt = self.next_pass(s)
        return nxt[1] if nxt else None

    def next_pass(self, s: float, min_ahead_m: float = 0.5) -> Optional[tuple[float, str]]:
        """(forward distance, stop_id) of the next stop pass strictly ahead of ``s``."""
        if not self._pass_offsets:
            return None
        s %= self.length_m
        i = bisect.bisect_right(self._pass_offsets, s + min_ahead_m)
        if i < len(self._pass_offsets):
            return self._pass_offsets[i] - s, self._pass_stops[i]
        return self._pass_offsets[0] + self.length_m - s, self._pass_stops[0]  # wrap to the loop start

    def passes_between(self, s: float, distance: float) -> int:
        """Number of stop passes in the half-open stretch (s, s + distance)."""
        if not self._pass_offsets or distance <= 0:
            return 0
        s %= self.length_m
        end = s + distance
        count = bisect.bisect_left(self._pass_offsets, min(end, self.length_m)) - bisect.bisect_right(self._pass_offsets, s)
        if end > self.length_m:  # wraps past the loop start
            count += bisect.bisect_left(self._pass_offsets, end - self.length_m)
        return max(0, count)

    def distance_to_stop(self, s: float, stop_id: str) -> Optional[float]:
        """Forward distance from ``s`` to the next pass of ``stop_id`` (None if not on route)."""
        offsets = self._passes_by_stop.get(stop_id)
        if not offsets:
            return None
        s %= self.length_m
        return min((o - s) % self.length_m for o in offsets)


@dataclass(frozen=True)
class RouteData:
    route: Route
    geometry: list[tuple[float, float]]
    geometry_lite: list[tuple[float, float]]
    stop_offsets: list[tuple[str, float]]


class TransitNetwork:
    def __init__(self, routes: list[RouteData], stops: list[Stop]) -> None:
        self._stops = {s.id: s for s in stops}
        self._routes = {r.route.id: r for r in routes}
        self._paths = {r.route.id: RoutePath(r.geometry, r.stop_offsets, r.route.closed) for r in routes}
        self._routes_by_stop: dict[str, list[str]] = {}
        for r in routes:
            for stop_id in dict.fromkeys(sid for sid, _ in r.stop_offsets):
                self._routes_by_stop.setdefault(stop_id, []).append(r.route.id)
        self._stop_keys = {s.id: normalize(s.name) for s in stops}

    # ---------- loading ----------

    @classmethod
    def from_dict(cls, data: dict) -> "TransitNetwork":
        stops = [Stop(id=s["id"], name=s["name"], lat=s["lat"], lng=s["lng"]) for s in data["stops"]]
        routes = []
        for r in data["routes"]:
            offsets = [(sid, float(o)) for sid, o in r["stops"]]
            geometry = [tuple(p) for p in r["geometry"]]
            routes.append(RouteData(
                route=Route(
                    id=r["id"], number=r["number"], name=r["name"], color=r["color"],
                    stop_count=len(dict.fromkeys(sid for sid, _ in offsets)),
                    length_m=r["length_m"], closed=r["closed"],
                ),
                geometry=geometry,
                geometry_lite=[tuple(p) for p in r.get("geometry_lite", geometry)],
                stop_offsets=offsets,
            ))
        return cls(routes, stops)

    @classmethod
    def from_file(cls, path: str | Path) -> "TransitNetwork":
        return cls.from_dict(json.loads(Path(path).read_text("utf-8")))

    # ---------- lookups ----------

    @property
    def routes(self) -> list[Route]:
        return [r.route for r in self._routes.values()]

    @property
    def stops(self) -> list[Stop]:
        return list(self._stops.values())

    def route(self, route_id: str) -> Optional[Route]:
        data = self._routes.get(route_id)
        return data.route if data else None

    def stop(self, stop_id: str) -> Optional[Stop]:
        return self._stops.get(stop_id)

    def path(self, route_id: str) -> RoutePath:
        return self._paths[route_id]

    def routes_serving(self, stop_id: str) -> list[Route]:
        return [self._routes[rid].route for rid in self._routes_by_stop.get(stop_id, [])]

    def route_detail(self, route_id: str) -> Optional[RouteDetail]:
        data = self._routes.get(route_id)
        if data is None:
            return None
        seen, stops = set(), []
        for stop_id, offset in data.stop_offsets:
            if stop_id not in seen:
                seen.add(stop_id)
                stops.append(StopOnRoute(**self._stops[stop_id].model_dump(), offset_m=offset))
        return RouteDetail(**data.route.model_dump(), stops=stops, geometry=data.geometry)

    def stop_detail(self, stop_id: str) -> Optional[StopDetail]:
        stop = self.stop(stop_id)
        if stop is None:
            return None
        return StopDetail(**stop.model_dump(), routes=self.routes_serving(stop_id))

    def lines(self) -> list[RouteLine]:
        return [RouteLine(id=r.route.id, number=r.route.number, color=r.route.color, geometry=r.geometry_lite)
                for r in self._routes.values()]

    # ---------- search ----------

    def search_stops(self, query: str, limit: int = 20) -> list[Stop]:
        q = normalize(query.strip())
        if not q:
            return []
        hits = [(0 if key.startswith(q) else 1, self._stops[sid].name, sid)
                for sid, key in self._stop_keys.items() if q in key]
        return [self._stops[sid] for *_, sid in sorted(hits)[:limit]]

    def search_routes(self, query: str, limit: int = 20) -> list[Route]:
        q = normalize(query.strip())
        if not q:
            return []
        ranked = []
        for r in self.routes:
            number = normalize(r.number)
            if number == q:
                rank = 0
            elif number.startswith(q):
                rank = 1
            elif q in number:
                rank = 2
            elif q in normalize(r.name):
                rank = 3
            else:
                continue
            ranked.append((rank, len(r.number), r.number, r.id))
        return [self._routes[rid].route for *_, rid in sorted(ranked)[:limit]]

    def nearest_stops(self, lat: float, lng: float, limit: int = 5) -> list[tuple[Stop, float]]:
        scored = sorted((haversine_m(lat, lng, s.lat, s.lng), s.id) for s in self._stops.values())
        return [(self._stops[sid], d) for d, sid in scored[:limit]]
