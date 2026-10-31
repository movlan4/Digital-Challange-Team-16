/* global L */
import { html, useEffect, useRef, useState } from '../lib.js';
import { busBadgeHtml, busIndex, iconHtml, Icon, LEVEL_COLOR, NO_DATA_COLOR } from './common.js';

const BAKU_CENTER = [40.40, 49.87];
const STOPS_MIN_ZOOM = 15;
const BUS_MIN_ZOOM = 15;       // individual buses only appear when zoomed in on a neighbourhood
const VIEWPORT_PADDING = 0.15; // render markers slightly outside the view so panning stays smooth

function stopIcon(state) {
  return L.divIcon({
    className: 'stop-marker-wrap',
    html: `<span class="stop-marker stop-${state}">${iconHtml('bus-stop')}</span>`,
    iconSize: null,
    iconAnchor: state === 'selected' ? [15, 15] : [11, 11],
  });
}

/**
 * Leaflet map of the bus network, optimised against clutter:
 *  - buses and stops are hidden below zoom 15; when visible, only the markers
 *    inside the current viewport exist (created / removed as you pan);
 *  - stops use the "bus stop" vector icon; stops of a selected line are always shown.
 *
 * Props:
 *   lines          overview polylines [{id, color, geometry}]; lineColor(line) colours them (e.g. by line density)
 *   routes         highlighted lines  [{id, color, number, geometry}]
 *   stops          all stops [{id, name, lat, lng}] - clickable when shown
 *   highlightStopIds  Set of stop ids always shown (e.g. stops of the selected line)
 *   buses          [{bus_id, route_number, lat, lng, level, dwelling, speed_kmh, density_source}]
 *   busColor(bus)  badge background colour
 *   selectedStopId, onStopClick(stopId), onBusClick(busId)
 *   focusKey / focusPoint   change focusKey to re-centre (focusPoint) or fit the highlighted routes
 */
export function TransitMap({
  lines = [], lineColor, routes = [], stops = [], highlightStopIds, buses = [], busColor,
  selectedStopId, onStopClick, onBusClick, focusKey, focusPoint, height = 520,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layers = useRef({});
  const busMarkers = useRef(new Map());
  const stopMarkers = useRef(new Map());
  const callbacks = useRef({});
  callbacks.current = { onStopClick, onBusClick };
  const [view, setView] = useState({ zoom: 12, bounds: null });

  // ---- init once
  useEffect(() => {
    if (typeof L === 'undefined') return undefined;
    const map = L.map(containerRef.current, { preferCanvas: true }).setView(BAKU_CENTER, 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map);
    layers.current = { lines: L.layerGroup().addTo(map), routes: L.layerGroup().addTo(map) };
    const onView = () => setView({ zoom: map.getZoom(), bounds: map.getBounds().pad(VIEWPORT_PADDING) });
    map.on('moveend', onView);  // fires after pans and zooms
    onView();
    mapRef.current = map;
    return () => map.remove();
  }, []);

  // ---- network overview (optionally coloured by line density)
  useEffect(() => {
    const layer = layers.current.lines;
    if (!layer) return;
    layer.clearLayers();
    lines.forEach((l) => {
      const color = lineColor ? lineColor(l) : '#64748b';
      L.polyline(l.geometry, { color, weight: lineColor ? 2.5 : 1.5, opacity: lineColor ? 0.7 : 0.45, interactive: false }).addTo(layer);
    });
  }, [lines, lineColor]);

  // ---- highlighted routes
  useEffect(() => {
    const layer = layers.current.routes;
    if (!layer) return;
    layer.clearLayers();
    routes.forEach((r) => {
      L.polyline(r.geometry, { color: '#ffffff', weight: 9, opacity: 0.9, interactive: false }).addTo(layer);
      L.polyline(r.geometry, { color: r.color, weight: 5, opacity: 0.95 }).bindTooltip(`Line ${r.number}`).addTo(layer);
    });
  }, [routes]);

  // ---- stops: "bus stop" icon markers, viewport-culled; highlighted ones always visible
  const zoomedIn = view.zoom >= STOPS_MIN_ZOOM;
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const markers = stopMarkers.current;
    const wanted = new Map();
    stops.forEach((s) => {
      const selected = s.id === selectedStopId;
      const highlighted = selected || (highlightStopIds && highlightStopIds.has(s.id));
      const inView = zoomedIn && view.bounds && view.bounds.contains([s.lat, s.lng]);
      if (highlighted || inView) wanted.set(s.id, { stop: s, state: selected ? 'selected' : highlighted ? 'route' : 'normal' });
    });
    for (const [id, marker] of markers) {
      if (!wanted.has(id)) { marker.remove(); markers.delete(id); }
    }
    wanted.forEach(({ stop, state }, id) => {
      let marker = markers.get(id);
      if (!marker) {
        marker = L.marker([stop.lat, stop.lng], { zIndexOffset: state === 'selected' ? 900 : 0 }).bindTooltip(stop.name);
        marker.on('click', () => callbacks.current.onStopClick?.(id));
        marker.addTo(map);
        markers.set(id, marker);
      }
      if (marker._state !== state) {
        marker.setIcon(stopIcon(state));
        marker.setZIndexOffset(state === 'selected' ? 900 : 0);
        marker._state = state;
      }
    });
  }, [stops, selectedStopId, highlightStopIds, view, zoomedIn]);

  // ---- refit / focus
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (focusPoint) {
      map.setView(focusPoint, Math.max(map.getZoom(), 16));
    } else if (routes.length) {
      const bounds = L.latLngBounds(routes.flatMap((r) => r.geometry));
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30] });
    }
  }, [focusKey]);

  // ---- buses: zoom-gated and viewport-culled; existing markers are moved, not recreated
  const busesVisible = view.zoom >= BUS_MIN_ZOOM;
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const markers = busMarkers.current;
    const visible = new Set();
    if (busesVisible && view.bounds) {
      buses.forEach((bus) => {
        if (!view.bounds.contains([bus.lat, bus.lng])) return;
        visible.add(bus.bus_id);
        const color = busColor ? busColor(bus) : '#2563eb';
        const key = `${color}|${bus.level}|${bus.dwelling}`;
        const state = bus.dwelling ? 'stopped at a stop' : `${bus.speed_kmh} km/h`;
        const tooltip = `Line ${bus.route_number}, bus ${busIndex(bus.bus_id)} · ${state}`;
        let marker = markers.get(bus.bus_id);
        if (!marker) {
          marker = L.marker([bus.lat, bus.lng], { zIndexOffset: 1000 }).bindTooltip(tooltip).addTo(map);
          marker.on('click', () => callbacks.current.onBusClick?.(bus.bus_id));
          markers.set(bus.bus_id, marker);
        } else {
          marker.setLatLng([bus.lat, bus.lng]);
          marker.setTooltipContent(tooltip);
        }
        if (marker._styleKey !== key) {
          marker.setIcon(L.divIcon({
            className: 'bus-marker', iconSize: null, iconAnchor: [26, 12],
            html: busBadgeHtml(bus.route_number, busIndex(bus.bus_id), color, bus.level, bus.dwelling),
          }));
          marker._styleKey = key;
        }
      });
    }
    for (const [id, marker] of markers) {
      if (!visible.has(id)) { marker.remove(); markers.delete(id); }
    }
  }, [buses, busColor, view, busesVisible]);

  if (typeof L === 'undefined') {
    return html`<div class="map-fallback" style=${{ height }}>Map library could not be loaded (offline?). Live data is still shown in the panels.</div>`;
  }
  const zoomIn = () => mapRef.current?.setZoom(BUS_MIN_ZOOM);
  return html`
    <div class="map-wrap" style=${{ height }}>
      <div ref=${containerRef} class="map" style=${{ height }}></div>
      ${!busesVisible && buses.length > 0 && html`
        <button class="map-hint" onClick=${zoomIn}><${Icon} name="zoom-in" /> Zoom in to see ${buses.length} bus${buses.length === 1 ? '' : 'es'} and stops</button>`}
    </div>`;
}

export const densityColor = (bus) => LEVEL_COLOR[bus.level] || NO_DATA_COLOR;
