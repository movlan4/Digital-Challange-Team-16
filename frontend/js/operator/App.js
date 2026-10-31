import { api } from '../api.js';
import { useSession, useLiveStream, useToasts } from '../hooks.js';
import { html, useState, useEffect, useMemo, useCallback, useRef } from '../lib.js';
import { TransitMap, densityColor } from '../components/TransitMap.js';
import {
  BusBadge, Header, Icon, LevelBadge, Loading, StatusBadge, Toasts, LEVEL_COLOR, NO_DATA_COLOR, busIndex, timeAgo,
} from '../components/common.js';

const DENSITY_REFRESH_MS = 5000;
const upsert = (list, item) => [item, ...list.filter((x) => x.id !== item.id)].sort((a, b) => b.id - a.id);
const busLabel = (busId, routeNumber) => `line ${routeNumber}, bus ${busIndex(busId)}`;

function Kpi({ icon, label, value, tone }) {
  return html`<div class=${`kpi ${tone ? `kpi-${tone}` : ''}`}>
    <span><${Icon} name=${icon} /> ${label}</span><strong>${value}</strong></div>`;
}

// ------------------------------------------------------------------ reports inbox

function ReportItem({ report, routesById, stopsById, fresh, onUpdate }) {
  const [resolving, setResolving] = useState(false);
  const [note, setNote] = useState('');
  const route = routesById[report.route_id];
  return html`
    <li class=${`report report-${report.status.toLowerCase()} ${fresh ? 'fresh' : ''}`}>
      <div class="report-head">
        <${BusBadge} number=${report.route_number} index=${busIndex(report.bus_id)} color=${route?.color} />
        <${LevelBadge} level=${report.level} />
        <span class="spacer"></span>
        <${StatusBadge} status=${report.status} />
      </div>
      <div class="report-meta muted small">
        #${report.id} · ${report.passenger_email} · ${timeAgo(report.created_at)}
        ${report.stop_id && html` · <${Icon} name="bus-stop" /> ${stopsById[report.stop_id]?.name || report.stop_id}`}
      </div>
      ${report.comment && html`<p class="report-comment"><${Icon} name="message" /> ${report.comment}</p>`}
      ${report.status === 'RESOLVED' && html`
        <p class="muted small"><${Icon} name="circle-check" /> Resolved by ${report.handled_by}${report.resolution_note ? `: ${report.resolution_note}` : ''}</p>`}
      ${report.status !== 'RESOLVED' && !resolving && html`
        <div class="row">
          ${report.status === 'OPEN' && html`
            <button class="btn btn-small" onClick=${() => onUpdate(report.id, 'IN_PROGRESS')}><${Icon} name="player-play" /> Start handling</button>`}
          <button class="btn btn-small btn-primary" onClick=${() => setResolving(true)}><${Icon} name="check" /> Resolve…</button>
        </div>`}
      ${resolving && html`
        <div class="row">
          <input class="grow" placeholder="Resolution note (e.g. reserve bus dispatched)" value=${note} maxlength="500"
                 onInput=${(e) => setNote(e.target.value)} />
          <button class="btn btn-small btn-primary" onClick=${() => onUpdate(report.id, 'RESOLVED', note)}>Confirm</button>
          <button class="btn btn-small btn-ghost" onClick=${() => setResolving(false)}>Cancel</button>
        </div>`}
    </li>`;
}

function ReportsInbox({ reports, routesById, stopsById, freshIds, onUpdate }) {
  const [tab, setTab] = useState('OPEN');
  const counts = useMemo(() => reports.reduce((c, r) => ({ ...c, [r.status]: (c[r.status] || 0) + 1 }), {}), [reports]);
  const shown = tab === 'ALL' ? reports : reports.filter((r) => r.status === tab);
  return html`
    <section class="card">
      <div class="card-title-row">
        <h2><${Icon} name="message-report" /> Passenger reports</h2>
        <span class="muted small">live · stay open until handled</span>
      </div>
      <div class="segmented">
        ${[['OPEN', 'Open'], ['IN_PROGRESS', 'In progress'], ['RESOLVED', 'Resolved'], ['ALL', 'All']].map(([key, label]) => html`
          <button key=${key} class=${tab === key ? 'active' : ''} onClick=${() => setTab(key)}>
            ${label}${key !== 'ALL' ? ` (${counts[key] || 0})` : ''}
          </button>`)}
      </div>
      ${shown.length === 0 && html`<p class="muted">No reports here.</p>`}
      <ul class="reports">
        ${shown.slice(0, 60).map((r) => html`
          <${ReportItem} key=${r.id} report=${r} routesById=${routesById} stopsById=${stopsById}
                         fresh=${freshIds.has(r.id)} onUpdate=${onUpdate} />`)}
      </ul>
    </section>`;
}

// ------------------------------------------------------------------ alerts

function AlertsPanel({ alerts, routesById, onAcknowledge, onResolve }) {
  const open = alerts.filter((a) => a.status !== 'RESOLVED');
  return html`
    <section class="card">
      <h2><${Icon} name="alert-triangle" /> Anomaly alerts</h2>
      ${open.length === 0 && html`<p class="muted">No open alerts. A bus that stays HIGH for 10 continuous minutes raises one.</p>`}
      ${open.map((a) => html`
        <div key=${a.id} class=${`alert alert-${a.status.toLowerCase()}`}>
          <div class="alert-head">
            <${BusBadge} number=${routesById[a.route_id]?.number} index=${busIndex(a.bus_id)} color=${routesById[a.route_id]?.color} level="HIGH" />
            <strong>${a.status === 'ACTIVE'
              ? html`<${Icon} name="alert-triangle" /> Additional bus reserve must be allocated`
              : html`<${Icon} name="circle-check" /> Acknowledged - handling`}</strong>
          </div>
          <p>${a.message}</p>
          <div class="alert-foot">
            <span class="muted">HIGH since ${new Date(a.high_since).toLocaleTimeString()} · raised ${timeAgo(a.created_at)}</span>
            <span class="row">
              ${a.status === 'ACTIVE' && html`<button class="btn btn-small" onClick=${() => onAcknowledge(a.id)}>Acknowledge</button>`}
              <button class="btn btn-small btn-primary" onClick=${() => onResolve(a.id)}>Resolve</button>
            </span>
          </div>
        </div>`)}
    </section>`;
}

// ------------------------------------------------------------------ line density

function LinesPanel({ lineDensity, routesById, onSelectLine, selectedLineId }) {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const rows = lineDensity.filter((d) => !q || d.route_number.toLowerCase().includes(q) || d.route_name.toLowerCase().includes(q));
  return html`
    <section class="card">
      <div class="card-title-row">
        <h2><${Icon} name="route" /> Line crowding</h2>
        <input type="search" placeholder="Line…" value=${query} onInput=${(e) => setQuery(e.target.value)} class="narrow" />
      </div>
      <div class="table-wrap lines-wrap">
        <table class="table">
          <thead><tr><th>Line</th><th>Crowding</th><th>Buses at High</th><th>Avg stop time</th><th>Reports</th></tr></thead>
          <tbody>
            ${rows.slice(0, 80).map((d) => html`
              <tr key=${d.route_id} class=${d.route_id === selectedLineId ? 'selected' : ''} onClick=${() => onSelectLine(d.route_id)}
                  title=${d.route_name}>
                <td><${BusBadge} number=${d.route_number} color=${routesById[d.route_id]?.color} size="sm" /></td>
                <td><${LevelBadge} level=${d.level} /></td>
                <td>${d.high_bus_count} / ${d.bus_count}</td>
                <td>${d.avg_dwell_s != null ? `${Math.round(d.avg_dwell_s)} s` : '-'}</td>
                <td>${d.report_count}</td>
              </tr>`)}
          </tbody>
        </table>
      </div>
      <p class="muted small">Mean of the line's buses. Each bus combines passenger reports with GPS behaviour.</p>
    </section>`;
}

// ------------------------------------------------------------------ per-bus density

function DensityTable({ density, routesById, openReportsByBus, selectedBusId, onSelect }) {
  const [query, setQuery] = useState('');
  const [level, setLevel] = useState('ESTIMATED');
  const q = query.trim().toLowerCase();
  const matchesLevel = (d) => (level === 'ESTIMATED' ? d.level !== null
    : level === 'REPORTED' ? d.report_count > 0 : !level || d.level === level);
  const rows = density.filter((d) => matchesLevel(d) && (!q || d.bus_id.toLowerCase().includes(q)));
  return html`
    <section class="card">
      <div class="card-title-row">
        <h2><${Icon} name="users-group" /> Crowdedness by bus</h2>
        <div class="row">
          <input type="search" placeholder="Line number…" value=${query} onInput=${(e) => setQuery(e.target.value)} />
          <select value=${level} onChange=${(e) => setLevel(e.target.value)}>
            <option value="ESTIMATED">With estimate (${density.filter((d) => d.level).length})</option>
            <option value="REPORTED">With passenger reports (${density.filter((d) => d.report_count).length})</option>
            <option value="">All buses (${density.length})</option>
            <option value="HIGH">High</option><option value="MEDIUM">Medium</option><option value="LOW">Low</option>
          </select>
        </div>
      </div>
      ${rows.length === 0 && html`<p class="muted">No buses match.</p>`}
      ${rows.length > 0 && html`
        <div class="table-wrap">
          <table class="table">
            <thead><tr>
              <th>Bus</th><th>Line</th><th>Crowdedness</th>
              <th title="Mean stop duration over the last stops">GPS: stop time</th><th>GPS: speed</th>
              <th title="Gap to the bus ahead vs. even spacing (1.0 = on schedule)">GPS: gap ahead</th>
              <th>Reports</th><th>Open</th><th>HIGH streak → alert</th>
            </tr></thead>
            <tbody>
              ${rows.slice(0, 100).map((d) => html`
                <tr key=${d.bus_id} class=${d.bus_id === selectedBusId ? 'selected' : ''} onClick=${() => onSelect(d.bus_id)}>
                  <td><${BusBadge} number=${d.route_number} index=${busIndex(d.bus_id)} color=${routesById[d.route_id]?.color} level=${d.level} /></td>
                  <td class="ellipsis">${routesById[d.route_id]?.name}</td>
                  <td><${LevelBadge} level=${d.level} count=${d.report_count} source=${d.source} /></td>
                  <td>${d.dwelling ? html`<span class="at-stop"><${Icon} name="hand-stop" /> at stop</span>` : d.avg_dwell_s != null ? `${Math.round(d.avg_dwell_s)} s` : '-'}</td>
                  <td>${d.moving_speed_kmh} km/h</td>
                  <td>${d.gap_ratio != null ? `${d.gap_ratio.toFixed(2)}×` : '-'}</td>
                  <td>${d.report_count}</td>
                  <td>${openReportsByBus[d.bus_id] || 0}</td>
                  <td>${d.high_streak_minutes.toFixed(1)} / ${d.anomaly_threshold_minutes} min</td>
                </tr>`)}
            </tbody>
          </table>
        </div>`}
      <p class="muted small">Hybrid estimate: passenger reports count only once at least 3 different passengers agree within 15 min (one vote per passenger, lone outliers ignored); their weight then grows with the evidence, up to 85 %, against GPS telemetry (long stops, slow driving, large gap to the bus ahead). GPS is used alone until then.</p>
    </section>`;
}

// ------------------------------------------------------------------ main screen

function OperatorHome({ session }) {
  const [toasts, toast] = useToasts();
  const [routes, setRoutes] = useState([]);
  const [stops, setStops] = useState([]);
  const [lines, setLines] = useState([]);
  const [buses, setBuses] = useState([]);
  const [lineDensity, setLineDensity] = useState([]);
  const [reports, setReports] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [density, setDensity] = useState([]);
  const [freshIds, setFreshIds] = useState(new Set());
  const [selectedBusId, setSelectedBusId] = useState(null);
  const [selectedLineId, setSelectedLineId] = useState(null);
  const ready = useRef(false);

  const { expired } = session;
  const handleError = useCallback((e) => {
    if (e.status === 401) expired(); else toast(e.message, 'error');
  }, [expired, toast]);

  useEffect(() => {
    Promise.all([api.routes(), api.stops(), api.lines()])
      .then(([r, s, l]) => { setRoutes(r); setStops(s); setLines(l); }).catch(handleError);
  }, [handleError]);

  useEffect(() => {
    const load = () => api.density().then(setDensity).catch((e) => e.status === 401 && expired());
    load();
    const t = setInterval(load, DENSITY_REFRESH_MS);
    return () => clearInterval(t);
  }, [expired]);

  const markFresh = (id) => {
    setFreshIds((s) => new Set(s).add(id));
    setTimeout(() => setFreshIds((s) => { const n = new Set(s); n.delete(id); return n; }), 8000);
  };

  const poll = useCallback(async () => {
    const [b, d, r, a] = await Promise.all([api.buses(), api.lineDensity(), api.reports(), api.alerts()]);
    setBuses(b); setLineDensity(d); setReports(r); setAlerts(a);
  }, []);
  const sortLines = (list) => [...list].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  const connected = useLiveStream('operator', {
    snapshot: (d) => { setBuses(d.buses); setLineDensity(sortLines(d.routes)); setReports(d.reports); setAlerts(d.alerts); ready.current = true; },
    buses: setBuses,
    routes: (list) => setLineDensity(sortLines(list)),
    report: (r) => {
      setReports((list) => {
        const isNew = !list.some((x) => x.id === r.id);
        if (isNew && ready.current) {
          markFresh(r.id);
          toast(`New report: ${busLabel(r.bus_id, r.route_number)} is ${r.level.toLowerCase()}${r.comment ? ` - “${r.comment}”` : ''}`, r.level === 'HIGH' ? 'error' : 'info');
        }
        return upsert(list, r);
      });
    },
    alert: (a) => {
      setAlerts((list) => upsert(list, a));
      if (a.status === 'ACTIVE') toast(`Alert: ${a.message}`, 'error');
    },
  }, poll);

  const routesById = useMemo(() => Object.fromEntries(routes.map((r) => [r.id, r])), [routes]);
  const stopsById = useMemo(() => Object.fromEntries(stops.map((s) => [s.id, s])), [stops]);
  const lineLevel = useMemo(() => Object.fromEntries(lineDensity.map((d) => [d.route_id, d.level])), [lineDensity]);
  const lineColor = useCallback((l) => LEVEL_COLOR[lineLevel[l.id]] || NO_DATA_COLOR, [lineLevel]);
  const openReportsByBus = useMemo(() => reports.reduce((m, r) => (r.status !== 'RESOLVED' ? { ...m, [r.bus_id]: (m[r.bus_id] || 0) + 1 } : m), {}), [reports]);

  const openCount = reports.filter((r) => r.status === 'OPEN').length;
  const progressCount = reports.filter((r) => r.status === 'IN_PROGRESS').length;
  const activeAlerts = alerts.filter((a) => a.status === 'ACTIVE').length;
  const highBuses = buses.filter((b) => b.level === 'HIGH').length;
  const highLines = lineDensity.filter((d) => d.level === 'HIGH').length;

  useEffect(() => {
    document.title = openCount || activeAlerts ? `(${openCount + activeAlerts}) Operator · Smart Transit` : 'Operator · Smart Transit';
  }, [openCount, activeAlerts]);

  const updateReport = async (id, status, note) => {
    try {
      const updated = await api.updateReport(id, status, note);
      setReports((list) => upsert(list, updated));
    } catch (e) { handleError(e); }
  };
  const alertAction = (fn) => async (id) => {
    try { const a = await fn(id); setAlerts((list) => upsert(list, a)); } catch (e) { handleError(e); }
  };
  const selectedBus = buses.find((b) => b.bus_id === selectedBusId);
  const selectedLine = useMemo(() => {
    const l = lines.find((x) => x.id === selectedLineId);
    return l ? [{ ...l, number: routesById[l.id]?.number, color: routesById[l.id]?.color }] : [];
  }, [lines, selectedLineId, routesById]);

  return html`
    <${Header} subtitle="Operator control room" connected=${connected}
               identity=${session.identity} onSignOut=${session.signOut}
               links=${session.identity.role === 'admin' ? [{ href: '/admin.html', label: 'User management', icon: 'users' }] : []} />
    <main class="operator">
      <div class="kpis">
        <${Kpi} icon="message-report" label="Open reports" value=${openCount} tone=${openCount ? 'warn' : 'ok'} />
        <${Kpi} icon="progress" label="In progress" value=${progressCount} />
        <${Kpi} icon="alert-triangle" label="Active alerts" value=${activeAlerts} tone=${activeAlerts ? 'danger' : 'ok'} />
        <${Kpi} icon="bus" label="Buses at High" value=${`${highBuses} / ${buses.length}`} tone=${highBuses ? 'warn' : 'ok'} />
        <${Kpi} icon="route" label="Lines at High" value=${`${highLines} / ${lineDensity.length}`} tone=${highLines ? 'warn' : 'ok'} />
      </div>
      <div class="operator-grid">
        <section class="card">
          <div class="card-title-row">
            <h2><${Icon} name="map-2" /> Live network crowding</h2>
            <span class="legend">
              <i style=${{ background: LEVEL_COLOR.LOW }}></i>Low <i style=${{ background: LEVEL_COLOR.MEDIUM }}></i>Medium
              <i style=${{ background: LEVEL_COLOR.HIGH }}></i>High <i style=${{ background: NO_DATA_COLOR }}></i>No data
            </span>
          </div>
          <${TransitMap} lines=${lines} lineColor=${lineColor} routes=${selectedLine} stops=${stops} buses=${buses} busColor=${densityColor}
                         onBusClick=${setSelectedBusId}
                         focusKey=${selectedBusId ? `bus:${selectedBusId}` : selectedLineId ? `line:${selectedLineId}` : 'none'}
                         focusPoint=${selectedBus ? [selectedBus.lat, selectedBus.lng] : null} height=${560} />
          ${selectedBus && html`
            <div class="selected-bus">
              <${BusBadge} number=${selectedBus.route_number} index=${busIndex(selectedBus.bus_id)} color=${routesById[selectedBus.route_id]?.color} level=${selectedBus.level} />
              <span>${routesById[selectedBus.route_id]?.name}</span>
              <${LevelBadge} level=${selectedBus.level} count=${selectedBus.report_count} source=${selectedBus.density_source} />
              <span class="muted small">
                ${selectedBus.dwelling ? html`<${Icon} name="hand-stop" /> stopped at` : `${selectedBus.speed_kmh} km/h · next stop`}
                ${' '}${stopsById[selectedBus.next_stop_id]?.name || '-'}
              </span>
            </div>`}
        </section>
        <div class="stack">
          <${AlertsPanel} alerts=${alerts} routesById=${routesById}
                          onAcknowledge=${alertAction(api.acknowledgeAlert)} onResolve=${alertAction(api.resolveAlert)} />
          <${ReportsInbox} reports=${reports} routesById=${routesById} stopsById=${stopsById}
                           freshIds=${freshIds} onUpdate=${updateReport} />
        </div>
      </div>
      <div class="operator-grid-bottom">
        <${LinesPanel} lineDensity=${lineDensity} routesById=${routesById} selectedLineId=${selectedLineId}
                       onSelectLine=${(id) => { setSelectedLineId(id); setSelectedBusId(null); }} />
        <${DensityTable} density=${density} routesById=${routesById} openReportsByBus=${openReportsByBus}
                         selectedBusId=${selectedBusId} onSelect=${(id) => { setSelectedBusId(id); setSelectedLineId(null); }} />
      </div>
    </main>
    <${Toasts} toasts=${toasts} />`;
}

export function OperatorApp() {
  const session = useSession(['operator', 'admin']);
  if (!session.identity) return html`<${Loading} />`;
  return html`<${OperatorHome} session=${session} />`;
}
