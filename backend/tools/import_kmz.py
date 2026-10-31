"""Build app/data/network.json from the official bus-line KMZ + OpenStreetMap.

The KMZ (BusLinesAll) contains one MultiGeometry/LineString placemark per bus
line, with only the line number as attribute - no stops. This tool:

1. Parses every line, chains multi-part geometries in travel order.
2. Fills gaps in the provided geometry (between parts, or long jumps inside a
   part) with road-following segments from OSRM (OpenStreetMap road network).
3. Downloads all bus stops in the network's bounding box from OpenStreetMap
   (Overpass API) and snaps each stop to every line passing within 35 m.
   If Overpass is unreachable, stops are derived from the line geometry
   (one every ~500 m, merged where lines share a road so a stop serves all
   lines passing it) and named after the OSM street they snap to (OSRM).
4. Simplifies the geometry (Douglas-Peucker) and writes a compact JSON file
   that the backend loads at start-up - the running app needs no internet.

Network responses are cached in tools/cache/ so re-runs are reproducible.

Usage:
    python tools/import_kmz.py path/to/buslines.kmz [--offline]
"""
from __future__ import annotations

import argparse
import bisect
import colorsys
import hashlib
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"
OUTPUT = ROOT / "app" / "data" / "network.json"

KML_NS = "{http://www.opengis.net/kml/2.2}"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/"
USER_AGENT = "SmartTransitBaku-prototype/1.0 (Digital Azerbaijan Challenge)"

PART_GAP_M = 25          # parts further apart than this are joined via OSRM
INNER_GAP_M = 1000       # consecutive vertices further apart are a data gap
MAX_DETOUR = 2.5         # reject OSRM fills much longer than the straight gap
DERIVED_STOP_SPACING_M = 500
DERIVED_STOP_MERGE_M = 45
CLOSED_LOOP_M = 250      # first/last vertex closer than this -> circular line
STOP_SNAP_M = 35         # a stop serves a line if it is this close to it
SIMPLIFY_M = 3           # detailed geometry tolerance
SIMPLIFY_LITE_M = 25     # overview geometry tolerance (operator network map)

Point = tuple[float, float]  # (lat, lng)


# ---------------------------------------------------------------- geometry

def haversine(a: Point, b: Point) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


class LocalProjection:
    """Equirectangular projection to metres - accurate enough at city scale."""

    def __init__(self, lat0: float) -> None:
        self.kx = 111_320 * math.cos(math.radians(lat0))
        self.ky = 110_540

    def xy(self, p: Point) -> tuple[float, float]:
        return p[1] * self.kx, p[0] * self.ky


def simplify(points: list[Point], tolerance_m: float, proj: LocalProjection) -> list[Point]:
    """Iterative Douglas-Peucker."""
    if len(points) < 3:
        return points
    xy = [proj.xy(p) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = xy[i], xy[j]
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        best, best_d = -1, tolerance_m
        for k in range(i + 1, j):
            px, py = xy[k]
            if seg2 == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / seg2))
                d = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
            if d > best_d:
                best, best_d = k, d
        if best >= 0:
            keep[best] = True
            stack += [(i, best), (best, j)]
    return [p for p, k in zip(points, keep) if k]


def chain_parts(parts: list[list[Point]]) -> list[list[Point]]:
    """Order (and orient) line parts greedily so each starts near the previous end."""
    parts = [p for p in parts if len(p) >= 2]
    if len(parts) <= 1:
        return parts
    remaining = sorted(parts, key=len, reverse=True)
    chain = [remaining.pop(0)]
    while remaining:
        tail_end, head_start = chain[-1][-1], chain[0][0]
        best = None
        for idx, part in enumerate(remaining):
            for rev in (False, True):
                cand = part[::-1] if rev else part
                for at_tail in (True, False):
                    d = haversine(tail_end, cand[0]) if at_tail else haversine(cand[-1], head_start)
                    if best is None or d < best[0]:
                        best = (d, idx, cand, at_tail)
        _, idx, cand, at_tail = best
        remaining.pop(idx)
        if at_tail:
            chain.append(cand)
        else:
            chain.insert(0, cand)
    return chain


# ---------------------------------------------------------------- network IO

def fetch_json(url: str, data: bytes | None = None, retries: int = 3, timeout: float = 60) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception as exc:  # network hiccup / rate limit
            if attempt == retries - 1:
                raise
            print(f"   retry after error: {exc}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


class OsrmGapFiller:
    """Road-following geometry between two points from the OSM road graph."""

    def __init__(self, offline: bool) -> None:
        self.offline = offline
        self.cache_file = CACHE / "osrm_routes.json"
        self.cache: dict[str, list[Point]] = (
            json.loads(self.cache_file.read_text("utf-8")) if self.cache_file.exists() else {}
        )
        self.filled = self.fallback = 0

    def route(self, a: Point, b: Point) -> list[Point]:
        key = f"{a[0]:.5f},{a[1]:.5f};{b[0]:.5f},{b[1]:.5f}"
        if key not in self.cache:
            if self.offline:
                self.fallback += 1
                return [a, b]
            url = f"{OSRM_URL}{a[1]},{a[0]};{b[1]},{b[0]}?overview=full&geometries=geojson"
            try:
                data = fetch_json(url)
                coords = data["routes"][0]["geometry"]["coordinates"]
                self.cache[key] = [[round(lat, 6), round(lng, 6)] for lng, lat in coords]
            except Exception as exc:
                print(f"   OSRM unavailable ({exc}); straight segment used", file=sys.stderr)
                self.fallback += 1
                return [a, b]
            time.sleep(1.0)  # public demo server: max ~1 request / second
        path = [a] + [tuple(p) for p in self.cache[key]] + [b]
        if sum(haversine(p, q) for p, q in zip(path, path[1:])) > MAX_DETOUR * haversine(a, b) + 200:
            self.fallback += 1  # one-way detour etc. - the straight source segment is more plausible
            return [a, b]
        self.filled += 1
        return path

    def street_names(self, points: list[Point], chunk: int = 90) -> list[str | None]:
        """OSM street name each point snaps to (OSRM waypoint names, cached)."""
        cache_file = CACHE / "osrm_street_names.json"
        cache = json.loads(cache_file.read_text("utf-8")) if cache_file.exists() else {}
        keys = [f"{p[0]:.5f},{p[1]:.5f}" for p in points]
        missing = [i for i, k in enumerate(keys) if k not in cache]
        if missing and not self.offline:
            print(f"-> naming {len(missing)} stops from OSM street names (OSRM)...", flush=True)
            for start in range(0, len(missing), chunk):
                idx = missing[start:start + chunk]
                coords = ";".join(f"{points[i][1]:.6f},{points[i][0]:.6f}" for i in idx)
                try:
                    data = fetch_json(f"{OSRM_URL}{coords}?overview=false&steps=false")
                    for i, wp in zip(idx, data["waypoints"]):
                        cache[keys[i]] = wp.get("name") or ""
                except Exception as exc:
                    print(f"   street naming failed for a chunk ({exc})", file=sys.stderr)
                time.sleep(1.0)
            CACHE.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
        return [cache.get(k) or None for k in keys]

    def save(self) -> None:
        CACHE.mkdir(exist_ok=True)
        self.cache_file.write_text(json.dumps(self.cache), "utf-8")


def load_osm_stops(bbox: tuple[float, float, float, float], offline: bool) -> list[dict] | None:
    """Bus stop nodes from Overpass, or None if no Overpass instance is reachable."""
    cache_file = CACHE / "osm_bus_stops.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text("utf-8"))
    if offline:
        return None
    s, w, n, e = bbox
    query = f"""
    [out:json][timeout:180];
    (
      node["highway"="bus_stop"]({s},{w},{n},{e});
      node["public_transport"="platform"]["bus"="yes"]({s},{w},{n},{e});
      node["public_transport"="stop_position"]["bus"="yes"]({s},{w},{n},{e});
    );
    out body;
    """
    data = None
    for url in OVERPASS_URLS:
        print(f"-> downloading bus stops from OpenStreetMap ({url})...", flush=True)
        try:
            data = fetch_json(url, urllib.parse.urlencode({"data": query}).encode(), retries=1, timeout=90)
            break
        except Exception as exc:
            print(f"   unavailable: {exc}", file=sys.stderr)
    if data is None:
        return None
    stops = [
        {"osm_id": el["id"], "lat": el["lat"], "lng": el["lon"], "tags": el.get("tags", {})}
        for el in data.get("elements", []) if el.get("type") == "node"
    ]
    CACHE.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(stops, ensure_ascii=False), "utf-8")
    return stops


# ---------------------------------------------------------------- KMZ

def read_kmz(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as z:
        kml_name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
        root = ET.fromstring(z.read(kml_name))
    lines = []
    for pm in root.iter(KML_NS + "Placemark"):
        number = (pm.findtext(KML_NS + "name") or "").strip()
        desc = pm.findtext(KML_NS + "description") or ""
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", desc, re.S)]
        attrs = dict(zip(cells[1::2], cells[2::2]))
        parts = []
        for ls in pm.iter(KML_NS + "LineString"):
            raw = (ls.findtext(KML_NS + "coordinates") or "").split()
            pts = []
            for triple in raw:
                lng, lat, *_ = triple.split(",")
                pts.append((float(lat), float(lng)))
            parts.append(pts)
        if number and parts:
            lines.append({"number": number, "fid": attrs.get("FID"), "parts": parts,
                          "shape_length": float(attrs["Shape_Leng"]) if attrs.get("Shape_Leng") else None})
    return lines


def route_color(number: str) -> str:
    hue = int(hashlib.md5(number.encode()).hexdigest()[:4], 16) / 0xFFFF
    r, g, b = colorsys.hls_to_rgb(hue, 0.42, 0.75)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def stop_name(tags: dict) -> str | None:
    return tags.get("name:az") or tags.get("name") or tags.get("name:en") or tags.get("name:ru")


# ---------------------------------------------------------------- snapping

class SegmentIndex:
    """Uniform grid over line segments for fast stop -> line proximity queries."""

    CELL = 0.002  # degrees (~200 m)

    def __init__(self, proj: LocalProjection) -> None:
        self.proj = proj
        self.grid: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self.routes: list[dict] = []

    def add_route(self, route_idx: int, geometry: list[Point], cumulative: list[float]) -> None:
        pad = STOP_SNAP_M / 100_000 * 1.5
        for i in range(len(geometry) - 1):
            (la1, ln1), (la2, ln2) = geometry[i], geometry[i + 1]
            for cx in range(int((min(ln1, ln2) - pad) // self.CELL), int((max(ln1, ln2) + pad) // self.CELL) + 1):
                for cy in range(int((min(la1, la2) - pad) // self.CELL), int((max(la1, la2) + pad) // self.CELL) + 1):
                    self.grid.setdefault((cx, cy), []).append((route_idx, i))

    def near(self, p: Point, geometries: list[list[Point]], cumulatives: list[list[float]]) -> dict[int, list[float]]:
        """route_idx -> offsets (m along line) of passes within STOP_SNAP_M of p."""
        cell = (int(p[1] // self.CELL), int(p[0] // self.CELL))
        px, py = self.proj.xy(p)
        hits: dict[int, list[tuple[int, float]]] = {}
        for route_idx, i in self.grid.get(cell, ()):
            geom = geometries[route_idx]
            x1, y1 = self.proj.xy(geom[i])
            x2, y2 = self.proj.xy(geom[i + 1])
            dx, dy = x2 - x1, y2 - y1
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg2))
            d = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
            if d <= STOP_SNAP_M:
                seg_len = cumulatives[route_idx][i + 1] - cumulatives[route_idx][i]
                hits.setdefault(route_idx, []).append((i, cumulatives[route_idx][i] + t * seg_len))
        result = {}
        for route_idx, passes in hits.items():
            passes.sort(key=lambda x: x[1])
            merged: list[float] = []
            for _, offset in passes:  # adjacent segments report the same pass
                if not merged or offset - merged[-1] > 150:
                    merged.append(offset)
            result[route_idx] = merged
        return result


def cumulative(points: list[Point]) -> list[float]:
    out, total = [0.0], 0.0
    for a, b in zip(points, points[1:]):
        total += haversine(a, b)
        out.append(total)
    return out


def point_at(geometry: list[Point], cum: list[float], d: float) -> Point:
    i = max(0, min(bisect.bisect_right(cum, d) - 1, len(geometry) - 2))
    seg = cum[i + 1] - cum[i]
    t = 0.0 if seg == 0 else (d - cum[i]) / seg
    (a1, b1), (a2, b2) = geometry[i], geometry[i + 1]
    return a1 + (a2 - a1) * t, b1 + (b2 - b1) * t


def derive_stops(geometries: list[list[Point]], cumulatives: list[list[float]]) -> list[dict]:
    """A candidate stop every DERIVED_STOP_SPACING_M along each line, merged across
    lines that share a road, so one stop serves every line passing through it."""
    cell = DERIVED_STOP_MERGE_M / 111_000
    grid: dict[tuple[int, int], list[int]] = {}
    clusters: list[list[float]] = []  # [sum_lat, sum_lng, count]
    for geom, cum in zip(geometries, cumulatives):
        n = max(1, int(cum[-1] // DERIVED_STOP_SPACING_M))
        for k in range(n + 1):
            p = point_at(geom, cum, min(k * DERIVED_STOP_SPACING_M, cum[-1]))
            gx, gy = int(p[0] // cell), int(p[1] // cell)
            match = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for ci in grid.get((gx + dx, gy + dy), ()):
                        c = clusters[ci]
                        if haversine(p, (c[0] / c[2], c[1] / c[2])) <= DERIVED_STOP_MERGE_M:
                            match = ci
            if match is None:
                grid.setdefault((gx, gy), []).append(len(clusters))
                clusters.append([p[0], p[1], 1])
            else:
                c = clusters[match]
                c[0] += p[0]; c[1] += p[1]; c[2] += 1
    return [
        {"id": f"D{i + 1:05d}", "osm_id": None, "lat": c[0] / c[2], "lng": c[1] / c[2], "name": None}
        for i, c in enumerate(clusters)
    ]


def route_name(route: dict, stops: list[tuple[str, float]], length: float, names: dict[str, str]) -> str:
    """'<start> – <terminus>'. Most source lines are closed out-and-back loops, so the
    terminus is the named stop closest to the middle of the loop (the turnaround)."""
    named = [(sid, off) for sid, off in stops if sid in names]
    if len(named) < 2:
        return f"Line {route['number']}"
    start = names[named[0][0]]
    if route["closed"]:
        terminus = names[min(named, key=lambda x: abs(x[1] - length / 2))[0]]
    else:
        terminus = names[named[-1][0]]
    return f"{start} – {terminus}" if terminus != start else f"Line {route['number']} ({start})"


def disambiguate_names(stops: list[dict]) -> None:
    """'Nizami küç.' x3 -> 'Nizami küç. (1)', '(2)', '(3)' ordered west to east."""
    groups: dict[str, list[dict]] = {}
    for s in stops:
        groups.setdefault(s["name"], []).append(s)
    for name, group in groups.items():
        if len(group) > 1:
            for k, s in enumerate(sorted(group, key=lambda s: (s["lng"], s["lat"])), 1):
                s["name"] = f"{name} ({k})"


# ---------------------------------------------------------------- main

def build(kmz: Path, offline: bool) -> dict:
    lines = read_kmz(kmz)
    print(f"-> {len(lines)} line placemarks in {kmz.name}")
    all_pts = [p for line in lines for part in line["parts"] for p in part]
    lat0 = sum(p[0] for p in all_pts) / len(all_pts)
    proj = LocalProjection(lat0)
    osrm = OsrmGapFiller(offline)

    routes = []
    seen_numbers: dict[str, int] = {}
    for line in lines:
        parts = chain_parts(line["parts"])
        merged: list[Point] = []
        gaps = 0
        for part in parts:
            if merged and haversine(merged[-1], part[0]) > PART_GAP_M:
                merged += osrm.route(merged[-1], part[0])[1:-1]
                gaps += 1
            for p in part:
                if merged and haversine(merged[-1], p) > INNER_GAP_M:
                    merged += osrm.route(merged[-1], p)[1:-1]
                    gaps += 1
                merged.append(p)

        number = line["number"]
        seen_numbers[number] = seen_numbers.get(number, 0) + 1
        variant = seen_numbers[number]
        route_id = f"L{number}" if variant == 1 else f"L{number}-{variant}"

        geometry = simplify(merged, SIMPLIFY_M, proj)
        routes.append({
            "id": route_id, "number": number, "variant": variant,
            "color": route_color(number),
            "closed": haversine(geometry[0], geometry[-1]) < CLOSED_LOOP_M,
            "geometry": geometry,
            "geometry_lite": simplify(geometry, SIMPLIFY_LITE_M, proj),
            "parts_in_source": len(line["parts"]),
            "osm_gap_fills": gaps,
        })
    osrm.save()

    lats = [p[0] for r in routes for p in r["geometry"]]
    lngs = [p[1] for r in routes for p in r["geometry"]]
    bbox = (min(lats) - 0.005, min(lngs) - 0.005, max(lats) + 0.005, max(lngs) + 0.005)
    geometries = [r["geometry"] for r in routes]
    cumulatives = [cumulative(g) for g in geometries]
    index = SegmentIndex(proj)
    for i, (g, c) in enumerate(zip(geometries, cumulatives)):
        index.add_route(i, g, c)

    osm_stops = load_osm_stops(bbox, offline)
    if osm_stops is not None:
        stop_source = "OpenStreetMap bus stops (Overpass API)"
        candidates = [
            {"id": f"S{s['osm_id']}", "osm_id": s["osm_id"], "lat": s["lat"], "lng": s["lng"],
             "name": stop_name(s["tags"])}
            for s in osm_stops
        ]
        print(f"-> {len(candidates)} bus stop nodes from OpenStreetMap")
    else:
        stop_source = (f"Derived from line geometry every ~{DERIVED_STOP_SPACING_M} m, "
                       "named by OSM street (OSRM) - Overpass was unreachable")
        candidates = derive_stops(geometries, cumulatives)
        print(f"-> Overpass unreachable: derived {len(candidates)} stops from the line geometry", flush=True)

    stops_out = []
    route_stops: list[list[tuple[str, float]]] = [[] for _ in routes]
    for s in candidates:
        hits = index.near((s["lat"], s["lng"]), geometries, cumulatives)
        if not hits:
            continue  # stop not served by any line in the dataset
        stops_out.append({
            "id": s["id"], "osm_id": s["osm_id"], "name": s["name"],
            "lat": round(s["lat"], 6), "lng": round(s["lng"], 6),
        })
        for route_idx, offsets in hits.items():
            route_stops[route_idx] += [(s["id"], round(o, 1)) for o in offsets]

    unnamed = [s for s in stops_out if not s["name"]]
    if unnamed:
        for s, street in zip(unnamed, osrm.street_names([(s["lat"], s["lng"]) for s in unnamed])):
            s["name"] = street
    for s in stops_out:
        s["named"] = bool(s["name"])
        s["name"] = s["name"] or "Dayanacaq"
    base_names = {s["id"]: s["name"] for s in stops_out if s["named"]}  # before "(1)", "(2)" suffixes
    disambiguate_names(stops_out)

    for route, stops, cum in zip(routes, route_stops, cumulatives):
        stops.sort(key=lambda x: x[1])
        route["stops"] = stops
        route["length_m"] = round(cum[-1], 1)
        route["name"] = route_name(route, stops, cum[-1], base_names)
        route["geometry"] = [[round(a, 6), round(b, 6)] for a, b in route["geometry"]]
        route["geometry_lite"] = [[round(a, 5), round(b, 5)] for a, b in route["geometry_lite"]]

    print(f"-> {len(stops_out)} stops snapped to at least one line; "
          f"OSRM gap fills: {osrm.filled} (straight-line fallbacks: {osrm.fallback})")
    no_stops = [r["number"] for r in routes if len(r["stops"]) < 2]
    if no_stops:
        print(f"   lines with < 2 OSM stops: {len(no_stops)} -> {no_stops[:20]}")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "lines": kmz.name,
            "stops": stop_source,
            "gap_routing": "OSRM on OpenStreetMap road network (data (c) OpenStreetMap contributors, ODbL)",
        },
        "stops": stops_out,
        "routes": routes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kmz", type=Path)
    parser.add_argument("--offline", action="store_true", help="use cached OSM/OSRM responses only")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    network = build(args.kmz, args.offline)
    args.output.write_text(json.dumps(network, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"-> wrote {args.output} ({args.output.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
