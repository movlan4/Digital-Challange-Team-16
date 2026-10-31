import { api, newSubmissionId } from '../api.js';
import { useSession, useLiveStream, useDebounced, useToasts } from '../hooks.js';
import { html, useState, useEffect, useMemo, useCallback } from '../lib.js';
import { TransitMap } from '../components/TransitMap.js';
import {
  BusBadge, Header, Icon, LevelBadge, Loading, StatusBadge, Toasts, LEVELS, LEVEL_LABEL, busIndex, formatEta, timeAgo,
} from '../components/common.js';

const ARRIVALS_REFRESH_MS = 4000;

// ------------------------------------------------------------------ search

function SearchCard({ lineDensity, onPickStop, onPickRoute, onNearest, locating }) {
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState('all');
  const [results, setResults] = useState(null);
  const q = useDebounced(query.trim());

  useEffect(() => {
    if (!q) { setResults(null); return; }
    let cancelled = false;
    api.search(q).then((r) => { if (!cancelled) setResults(r); }).catch(() => {});
    return () => { cancelled = true; };
  }, [q]);

  const pick = (fn, id) => { fn(id); setQuery(''); setResults(null); };
  const showStops = scope !== 'routes';
  const showRoutes = scope !== 'stops';

  return html`
    <section class="card">
      <div class="search-box">
        <label class="input-icon">
          <${Icon} name="search" />
          <input type="search" placeholder="Search a bus stop or bus number…" value=${query}
                 onInput=${(e) => setQuery(e.target.value)} aria-label="Search stops and bus numbers" />
        </label>
        <button class="btn btn-ghost" onClick=${onNearest} disabled=${locating} title="Use my location">
          <${Icon} name=${locating ? 'loader-2' : 'current-location'} className=${locating ? 'spin' : ''} /> Nearest stop
        </button>
      </div>
      <div class="segmented" role="tablist">
        ${[['all', 'All', 'list-search'], ['stops', 'Bus stops', 'bus-stop'], ['routes', 'Bus numbers', 'bus']].map(([key, label, icon]) => html`
          <button key=${key} class=${scope === key ? 'active' : ''} onClick=${() => setScope(key)}><${Icon} name=${icon} /> ${label}</button>`)}
      </div>
      ${results && html`
        <div class="search-results">
          ${showRoutes && html`
            <h3>Bus numbers</h3>
            ${results.routes.length === 0 && html`<p class="muted small">No matching bus numbers</p>`}
            <div class="chip-list">
              ${results.routes.map((r) => html`
                <button key=${r.id} class="result-route" onClick=${() => pick(onPickRoute, r.id)}>
                  <${BusBadge} number=${r.number} color=${r.color} />
                  <span class="small grow">${r.name}</span>
                  <${LevelBadge} level=${lineDensity[r.id]?.level} />
                </button>`)}
            </div>`}
          ${showStops && html`
            <h3>Bus stops</h3>
            ${results.stops.length === 0 && html`<p class="muted small">No matching stops</p>`}
            <ul class="result-list">
              ${results.stops.map((s) => html`
                <li key=${s.id}><button onClick=${() => pick(onPickStop, s.id)}><${Icon} name="bus-stop" className="stop-icon" /> ${s.name}</button></li>`)}
            </ul>`}
        </div>`}
    </section>`;
}

// ------------------------------------------------------------------ stop view

function StopPanel({ stopId, routesById, lineDensity, onPickRoute, onReport }) {
  const [stop, setStop] = useState(null);
  const [arrivals, setArrivals] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setStop(null); setArrivals(null); setError(null);
    api.stop(stopId).then((s) => !cancelled && setStop(s)).catch((e) => !cancelled && setError(e.message));
    const load = () => api.arrivals(stopId).then((a) => !cancelled && setArrivals(a)).catch(() => {});
    load();
    const timer = setInterval(load, ARRIVALS_REFRESH_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [stopId]);

  if (error) return html`<section class="card"><p class="error">${error}</p></section>`;
  if (!stop) return html`<section class="card"><p class="muted">Loading stop…</p></section>`;

  return html`
    <section class="card">
      <p class="eyebrow">Your stop</p>
      <h2 class="with-icon"><${Icon} name="bus-stop" className="stop-icon" /> ${stop.name}</h2>
      <h3>Lines through this stop (${stop.routes.length})</h3>
      <div class="chip-list">
        ${stop.routes.map((r) => html`
          <${BusBadge} key=${r.id} number=${r.number} color=${r.color} level=${lineDensity[r.id]?.level}
                       onClick=${() => onPickRoute(r.id)} title=${`${r.name} · line crowding: ${lineDensity[r.id]?.level?.toLowerCase() || 'no data'}`} />`)}
      </div>
      <h3>Buses arriving</h3>
      ${!arrivals && html`<p class="muted">Loading live arrivals…</p>`}
      ${arrivals && arrivals.length === 0 && html`<p class="muted">No buses heading here right now.</p>`}
      <ul class="arrivals">
        ${(arrivals || []).map((a, i) => html`
          <li key=${a.bus_id} class=${i === 0 ? 'next' : ''}>
            <${BusBadge} number=${a.route_number} index=${busIndex(a.bus_id)} color=${routesById[a.route_id]?.color} level=${a.level} />
            <div class="arrival-main">
              <span class="small">
                ${a.dwelling ? html`<span class="at-stop"><${Icon} name="hand-stop" /> At a stop</span>` : `${(a.distance_m / 1000).toFixed(1)} km away`}
              </span>
              <${LevelBadge} level=${a.level} count=${a.report_count} source=${a.density_source} />
            </div>
            <span class="eta">${formatEta(a.eta_seconds)}</span>
            <button class="btn btn-small" onClick=${() => onReport(a.bus_id, stopId)}><${Icon} name="message-report" /> Report</button>
          </li>`)}
      </ul>
    </section>`;
}

// ------------------------------------------------------------------ line view

function RoutePanel({ route, buses, density, onPickStop, onReport, onLocateBus }) {
  if (!route) return html`<section class="card"><p class="muted">Loading line…</p></section>`;
  return html`
    <section class="card">
      <p class="eyebrow">Bus line</p>
      <div class="route-title">
        <${BusBadge} number=${route.number} color=${route.color} size="lg" />
        <div class="grow"><strong>${route.name}</strong>
          <div class="muted small">${route.stop_count} stops · ${(route.length_m / 1000).toFixed(1)} km</div></div>
      </div>
      <p class="line-density">Line crowding: <${LevelBadge} level=${density?.level} />
        ${density?.high_bus_count > 0 && html` <span class="muted small">${density.high_bus_count} bus(es) at High</span>`}</p>
      <h3>Buses on this line (${buses.length})</h3>
      <ul class="bus-list">
        ${buses.map((b) => html`
          <li key=${b.bus_id}>
            <${BusBadge} number=${b.route_number} index=${busIndex(b.bus_id)} color=${route.color} level=${b.level} />
            <button class="link-inline" onClick=${() => onLocateBus(b)} title="Show on map">
              <${Icon} name="crosshair" /> ${b.dwelling ? 'At a stop' : `${b.speed_kmh} km/h`}
            </button>
            <${LevelBadge} level=${b.level} count=${b.report_count} source=${b.density_source} />
            <button class="btn btn-small" onClick=${() => onReport(b.bus_id, null)} title="Report crowding"><${Icon} name="message-report" /></button>
          </li>`)}
      </ul>
      <h3>Stops</h3>
      <ol class="stop-list">
        ${route.stops.map((s) => html`<li key=${s.id}><button onClick=${() => onPickStop(s.id)}><${Icon} name="bus-stop" className="stop-icon" /> ${s.name}</button></li>`)}
      </ol>
    </section>`;
}

// ------------------------------------------------------------------ reporting

function ReportDialog({ bus, stopId, color, onClose, onSent }) {
  const [submissionId] = useState(newSubmissionId);  // one id per submission -> retries are deduplicated
  const [level, setLevel] = useState(null);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const receipt = await api.submitReport({
        bus_id: bus.bus_id, level, stop_id: stopId || null, comment: comment || null, submission_id: submissionId,
      });
      onSent(receipt);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return html`
    <div class="modal-backdrop" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="modal card" role="dialog" aria-modal="true" aria-label="Report crowdedness">
        <div class="route-title">
          <${BusBadge} number=${bus.route_number} index=${busIndex(bus.bus_id)} color=${color} size="lg" level=${bus.level} />
          <div><strong>How crowded is this bus?</strong>
            <div class="muted small">Line ${bus.route_number}, bus ${busIndex(bus.bus_id)} · your report goes straight to the operator control room.</div></div>
        </div>
        <div class="report-buttons">
          ${LEVELS.map((l) => html`
            <button key=${l} class=${`btn btn-level btn-${l.toLowerCase()} ${level === l ? 'selected' : ''}`}
                    onClick=${() => setLevel(l)} aria-pressed=${level === l}>${LEVEL_LABEL[l]}</button>`)}
        </div>
        <label class="field">
          <span>Comment (optional)</span>
          <textarea maxlength="280" rows="2" value=${comment} onInput=${(e) => setComment(e.target.value)}
                    placeholder="e.g. people left behind at the stop"></textarea>
        </label>
        ${error && html`<p class="error">${error}</p>`}
        <div class="row end">
          <button class="btn btn-ghost" onClick=${onClose}>Cancel</button>
          <button class="btn btn-primary" disabled=${!level || busy} onClick=${send}>
            <${Icon} name="send" /> ${busy ? 'Sending…' : 'Send report'}
          </button>
        </div>
      </div>
    </div>`;
}

function MyReports({ reports, routesById }) {
  if (!reports.length) return null;
  return html`
    <section class="card">
      <h2>My reports</h2>
      <ul class="my-reports">
        ${reports.slice(0, 6).map((r) => html`
          <li key=${r.id}>
            <span class="row"><${BusBadge} number=${r.route_number} index=${busIndex(r.bus_id)} color=${routesById[r.route_id]?.color} size="sm" />
              <${LevelBadge} level=${r.level} /></span>
            <span class="muted small">${timeAgo(r.created_at)}</span>
            <${StatusBadge} status=${r.status} />
          </li>`)}
      </ul>
    </section>`;
}

// ------------------------------------------------------------------ main screen

function PassengerHome({ session }) {
  const [toasts, toast] = useToasts();
  const [routes, setRoutes] = useState([]);
  const [stops, setStops] = useState([]);
  const [buses, setBuses] = useState([]);
  const [lineDensityList, setLineDensityList] = useState([]);
  const [selection, setSelection] = useState(null);  // {type: 'stop'|'route', id}
  const [routeDetail, setRouteDetail] = useState(null);
  const [servingRouteIds, setServingRouteIds] = useState(null);
  const [reporting, setReporting] = useState(null);  // {busId, stopId}
  const [myReports, setMyReports] = useState([]);
  const [locating, setLocating] = useState(false);
  const [focus, setFocus] = useState(null);  // {key, point} - explicit "show this on the map"

  const { expired } = session;
  const handleError = useCallback((e) => { if (e.status === 401) expired(); }, [expired]);

  useEffect(() => {
    Promise.all([api.routes(), api.stops()]).then(([r, s]) => { setRoutes(r); setStops(s); }).catch(handleError);
  }, [handleError]);

  const loadMyReports = useCallback(() => api.myReports().then(setMyReports).catch(handleError), [handleError]);
  useEffect(() => { loadMyReports(); const t = setInterval(loadMyReports, 15000); return () => clearInterval(t); }, [loadMyReports]);

  const poll = useCallback(async () => {
    const [b, d] = await Promise.all([api.buses(), api.lineDensity()]);
    setBuses(b); setLineDensityList(d);
  }, []);
  const connected = useLiveStream('passenger', {
    snapshot: (d) => { setBuses(d.buses); setLineDensityList(d.routes); },
    buses: setBuses,
    routes: setLineDensityList,
  }, poll);

  const routesById = useMemo(() => Object.fromEntries(routes.map((r) => [r.id, r])), [routes]);
  const stopsById = useMemo(() => Object.fromEntries(stops.map((s) => [s.id, s])), [stops]);
  const lineDensity = useMemo(() => Object.fromEntries(lineDensityList.map((d) => [d.route_id, d])), [lineDensityList]);

  // selected line geometry / stops
  useEffect(() => {
    setRouteDetail(null);
    if (selection?.type !== 'route') return;
    api.route(selection.id).then(setRouteDetail).catch(handleError);
  }, [selection, handleError]);

  // lines through the selected stop -> which buses to show on the map
  useEffect(() => {
    setServingRouteIds(null);
    if (selection?.type !== 'stop') return;
    api.stop(selection.id).then((s) => setServingRouteIds(new Set(s.routes.map((r) => r.id)))).catch(handleError);
  }, [selection, handleError]);

  const pickStop = useCallback((id) => { setSelection({ type: 'stop', id }); setFocus(null); }, []);
  const pickRoute = useCallback((id) => { setSelection({ type: 'route', id }); setFocus(null); }, []);
  const locateBus = (bus) => setFocus({ key: `bus:${bus.bus_id}:${Date.now()}`, point: [bus.lat, bus.lng] });

  const nearest = () => {
    if (!navigator.geolocation) { toast('Location is not available in this browser', 'warn'); return; }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(async (pos) => {
      try {
        const [stop] = await api.nearestStops(pos.coords.latitude, pos.coords.longitude);
        if (stop) { pickStop(stop.id); toast(`Nearest stop: ${stop.name}`, 'info'); }
      } catch (e) { toast(e.message, 'error'); } finally { setLocating(false); }
    }, () => { setLocating(false); toast('Location permission denied - pick your stop on the map', 'warn'); },
    { enableHighAccuracy: true, timeout: 8000 });
  };

  const visibleBuses = useMemo(() => {
    if (selection?.type === 'route') return buses.filter((b) => b.route_id === selection.id);
    if (selection?.type === 'stop') return servingRouteIds ? buses.filter((b) => servingRouteIds.has(b.route_id)) : [];
    return buses;
  }, [buses, selection, servingRouteIds]);

  const mapRoutes = useMemo(() => (routeDetail ? [routeDetail] : []), [routeDetail]);
  const highlightStopIds = useMemo(() => (routeDetail ? new Set(routeDetail.stops.map((s) => s.id)) : null), [routeDetail]);
  const busColor = useCallback((b) => routesById[b.route_id]?.color || '#2563eb', [routesById]);
  const selectedStop = selection?.type === 'stop' ? stopsById[selection.id] : null;
  const reportBus = reporting && buses.find((b) => b.bus_id === reporting.busId);

  const onSent = (receipt) => {
    setReporting(null);
    toast(receipt.duplicate ? 'This report was already received - not counted twice.' : `Report #${receipt.report.id} sent to the operators. Thank you!`, 'success');
    loadMyReports();
  };

  return html`
    <${Header} subtitle="Passenger · live buses & arrivals" connected=${connected}
               identity=${session.identity} onSignOut=${session.signOut} />
    <main class="layout">
      <aside class="sidebar">
        <${SearchCard} lineDensity=${lineDensity} onPickStop=${pickStop} onPickRoute=${pickRoute}
                       onNearest=${nearest} locating=${locating} />
        ${!selection && html`
          <section class="card hint">
            <h2>Where are you?</h2>
            <p class="muted">Search your stop, use <b><${Icon} name="current-location" /> Nearest stop</b>, or zoom in and tap a
              <${Icon} name="bus-stop" className="stop-icon" /> stop on the map to see every bus passing through it.</p>
          </section>`}
        ${selection?.type === 'stop' && html`
          <${StopPanel} stopId=${selection.id} routesById=${routesById} lineDensity=${lineDensity} onPickRoute=${pickRoute}
                        onReport=${(busId, stopId) => setReporting({ busId, stopId })} />`}
        ${selection?.type === 'route' && html`
          <${RoutePanel} route=${routeDetail} buses=${visibleBuses} density=${lineDensity[selection.id]}
                         onPickStop=${pickStop} onLocateBus=${locateBus}
                         onReport=${(busId) => setReporting({ busId, stopId: null })} />`}
        <${MyReports} reports=${myReports} routesById=${routesById} />
      </aside>
      <section class="content card">
        <div class="card-title-row">
          <h2>Live map</h2>
          <span class="muted small">Crowding combines passenger reports with GPS (stop delays, speed, bus spacing)</span>
        </div>
        <${TransitMap} routes=${mapRoutes} stops=${stops} highlightStopIds=${highlightStopIds} buses=${visibleBuses}
                       busColor=${busColor} selectedStopId=${selectedStop?.id} onStopClick=${pickStop}
                       onBusClick=${(busId) => setReporting({ busId, stopId: selectedStop?.id || null })}
                       focusKey=${focus ? focus.key : selection ? `${selection.type}:${selection.id}:${routeDetail ? 1 : 0}` : 'none'}
                       focusPoint=${focus ? focus.point : selectedStop ? [selectedStop.lat, selectedStop.lng] : null} height=${640} />
      </section>
    </main>
    ${reportBus && html`
      <${ReportDialog} bus=${reportBus} stopId=${reporting.stopId} color=${routesById[reportBus.route_id]?.color}
                       onClose=${() => setReporting(null)} onSent=${onSent} />`}
    <${Toasts} toasts=${toasts} />`;
}

export function PassengerApp() {
  const session = useSession(['passenger']);
  if (!session.identity) return html`<${Loading} />`;
  return html`<${PassengerHome} session=${session} />`;
}
