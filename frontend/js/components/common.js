import { html, useState } from '../lib.js';

export const LEVELS = ['LOW', 'MEDIUM', 'HIGH'];
export const LEVEL_LABEL = { LOW: 'Low', MEDIUM: 'Medium', HIGH: 'High' };
export const LEVEL_COLOR = { LOW: '#16a34a', MEDIUM: '#f59e0b', HIGH: '#dc2626' };
export const NO_DATA_COLOR = '#64748b';
export const STATUS_LABEL = { OPEN: 'Open', IN_PROGRESS: 'In progress', RESOLVED: 'Resolved' };
export const ROLE_LABEL = { passenger: 'Passenger', operator: 'Operator', admin: 'Master admin' };
export const LOGO_SRC = '/assets/logo.png';

/** Tabler icon (webfont). name = icon name without the "ti-" prefix, e.g. "bus-stop". */
export function Icon({ name, label, className = '' }) {
  return html`<i class=${`ti ti-${name} ${className}`} aria-hidden=${label ? undefined : 'true'} aria-label=${label} role=${label ? 'img' : undefined}></i>`;
}

/** Same icon as an HTML string (Leaflet markers). */
export const iconHtml = (name, className = '') => `<i class="ti ti-${name} ${className}" aria-hidden="true"></i>`;

/** Bus number within its line: "88-02" -> "2" (leading dashes and zeros stripped). */
export function busIndex(busId) {
  const index = String(busId).slice(String(busId).lastIndexOf('-') + 1).replace(/^[-0]+/, '');
  return index || '0';
}

/** Site logo; falls back to a bus icon + name if the image file is missing. */
export function Logo({ className = 'brand-logo' }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return html`<span class="brand-fallback"><${Icon} name="bus" /> Baku Smart Transit</span>`;
  }
  return html`<img class=${className} src=${LOGO_SRC} alt="Baku Smart Transit" onError=${() => setFailed(true)} />`;
}

/**
 * "Bus icon + line number" badge, with the bus's own number in a square
 * (e.g. [bus] 88 [2]). Used in lists, search results and map markers.
 */
export function BusBadge({ number, index, color = '#2563eb', level, size = 'md', onClick, title }) {
  const Tag = onClick ? 'button' : 'span';
  return html`
    <${Tag} class=${`bus-badge bus-badge-${size} ${onClick ? 'clickable' : ''}`} style=${{ background: color }}
            onClick=${onClick} title=${title || (index ? `Line ${number}, bus ${index}` : `Line ${number}`)}>
      <${Icon} name="bus" className="bus-badge-icon" />
      <span class="bus-badge-number">${number}</span>
      ${index && html`<span class="bus-index">${index}</span>`}
      ${level && html`<i class="bus-badge-dot" style=${{ background: LEVEL_COLOR[level] }} title=${`Crowdedness: ${LEVEL_LABEL[level]}`}></i>`}
    <//>`;
}

/** Same badge as an HTML string for Leaflet divIcons. */
export function busBadgeHtml(number, index, color, level, dwelling) {
  const dot = level ? `<i class="bus-badge-dot" style="background:${LEVEL_COLOR[level]}"></i>` : '';
  const stopped = dwelling ? ' is-dwelling' : '';
  return `<span class="bus-badge bus-badge-sm${stopped}" style="background:${color}">${iconHtml('bus', 'bus-badge-icon')}`
    + `<span class="bus-badge-number">${number}</span><span class="bus-index">${index}</span>${dot}</span>`;
}

const SOURCE_LABEL = { gps: 'GPS', reports: 'reports', 'reports+gps': 'reports + GPS' };

/** Crowdedness badge. source = where the level comes from; count = recent passenger reports. */
export function LevelBadge({ level, count, source }) {
  if (!level) return html`<span class="badge badge-none" title="No GPS history or passenger reports yet">No data</span>`;
  let detail = '';
  if (source === 'gps') detail = ' · GPS';
  else if (count) detail = ` · ${count} report${count === 1 ? '' : 's'}${source === 'reports+gps' ? ' + GPS' : ''}`;
  return html`<span class=${`badge badge-${level.toLowerCase()}`} title=${source ? `Estimated from ${SOURCE_LABEL[source]}` : ''}>
    ${LEVEL_LABEL[level]}${detail}</span>`;
}

export function StatusBadge({ status }) {
  return html`<span class=${`badge status-${status.toLowerCase()}`}>${STATUS_LABEL[status]}</span>`;
}

/** links: [{href, label, icon}] shown in the header (e.g. admin <-> operator dashboard). */
export function Header({ subtitle, connected, identity, onSignOut, links = [] }) {
  return html`
    <header class="app-header">
      <div class="brand">
        <${Logo} />
        <span class="brand-context">${subtitle}</span>
      </div>
      <div class="header-right">
        ${connected !== undefined && html`
          <span class=${`live ${connected ? 'live-on' : 'live-off'}`}>
            <${Icon} name=${connected ? 'antenna-bars-5' : 'antenna-bars-off'} /> ${connected ? 'Live' : 'Reconnecting…'}
          </span>`}
        ${links.map((l) => html`<a key=${l.href} class="btn btn-ghost" href=${l.href}><${Icon} name=${l.icon} /> ${l.label}</a>`)}
        ${identity && html`<span class="who"><${Icon} name="user-circle" /> ${identity.email} · ${ROLE_LABEL[identity.role]}</span>`}
        ${onSignOut && html`<button class="btn btn-ghost" onClick=${onSignOut}><${Icon} name="logout" /> Sign out</button>`}
      </div>
    </header>`;
}

export function Loading() {
  return html`<div class="center muted">Loading…</div>`;
}

export function Toasts({ toasts }) {
  return html`
    <div class="toasts" role="status" aria-live="polite">
      ${toasts.map((t) => html`<div key=${t.id} class=${`toast toast-${t.kind}`}>${t.message}</div>`)}
    </div>`;
}

export function formatEta(seconds) {
  if (seconds < 45) return 'Now';
  return `${Math.round(seconds / 60)} min`;
}

export function timeAgo(iso) {
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  return new Date(iso).toLocaleString();
}
