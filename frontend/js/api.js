// Thin client for the Smart Transit REST API. The session is an HttpOnly
// cookie set at login and sent automatically.
const API_BASE = window.TRANSIT_API_BASE || '';

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && body.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg.replace(/^Value error, /, '')).join(', ')
      : detail || res.statusText;
    throw new ApiError(res.status, message);
  }
  return body;
}

const qs = (params) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return entries.length ? `?${new URLSearchParams(entries)}` : '';
};
const post = (path, data) => request(path, { method: 'POST', body: JSON.stringify(data ?? {}) });
const patch = (path, data) => request(path, { method: 'PATCH', body: JSON.stringify(data ?? {}) });

export function newSubmissionId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

export const api = {
  // auth - one login for every role; the response's `home` is the role's dashboard
  me: () => request('/api/auth/me'),
  login: (email, password) => post('/api/auth/login', { email, password }),
  register: (email, password) => post('/api/auth/register', { email, password }),
  logout: () => post('/api/auth/logout'),
  forgotPassword: (email) => post('/api/auth/forgot-password', { email }),
  resetPassword: (email, code, newPassword) => post('/api/auth/reset-password', { email, code, new_password: newPassword }),

  // master admin
  users: (q) => request(`/api/admin/users${qs({ q })}`),
  setRole: (userId, role) => patch(`/api/admin/users/${userId}`, { role }),

  // shared network data
  search: (q) => request(`/api/search${qs({ q, limit: 8 })}`),
  stops: () => request('/api/stops'),
  stop: (id) => request(`/api/stops/${encodeURIComponent(id)}`),
  nearestStops: (lat, lng) => request(`/api/stops/nearest${qs({ lat, lng, limit: 1 })}`),
  arrivals: (stopId) => request(`/api/stops/${encodeURIComponent(stopId)}/arrivals${qs({ limit: 30 })}`),
  routes: () => request('/api/routes'),
  route: (id) => request(`/api/routes/${encodeURIComponent(id)}`),
  lines: () => request('/api/routes/lines'),
  lineDensity: () => request('/api/routes/density'),
  buses: (routeId) => request(`/api/buses${qs({ route_id: routeId })}`),

  // passenger
  submitReport: (payload) => post('/api/passenger/reports', payload),
  myReports: () => request('/api/passenger/reports'),

  // operator
  reports: (status) => request(`/api/operator/reports${qs({ status, limit: 200 })}`),
  updateReport: (id, status, note) => patch(`/api/operator/reports/${id}`, { status, note: note || null }),
  density: () => request('/api/operator/density?limit=1000'),
  alerts: () => request('/api/operator/alerts'),
  acknowledgeAlert: (id) => post(`/api/operator/alerts/${id}/acknowledge`),
  resolveAlert: (id) => post(`/api/operator/alerts/${id}/resolve`),

  streamUrl: (role) => `${API_BASE}${role === 'operator' ? '/api/operator/stream' : '/api/stream'}`,
};
