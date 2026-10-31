import { api } from '../api.js';
import { useSession, useDebounced, useToasts } from '../hooks.js';
import { html, useState, useEffect, useCallback } from '../lib.js';
import { Header, Icon, Loading, Toasts, ROLE_LABEL } from '../components/common.js';

const ASSIGNABLE = ['passenger', 'operator'];

function UserRow({ user, me, onChange }) {
  const [role, setRole] = useState(user.role);
  const [busy, setBusy] = useState(false);
  useEffect(() => setRole(user.role), [user.role]);
  const locked = user.role === 'admin' || user.id === me.user_id;

  const save = async () => {
    setBusy(true);
    try { await onChange(user, role); } finally { setBusy(false); }
  };

  return html`
    <tr>
      <td>${user.id}</td>
      <td>${user.email}</td>
      <td><span class=${`badge role-${user.role}`}>${ROLE_LABEL[user.role]}</span></td>
      <td class="muted small">${new Date(user.created_at).toLocaleString()}</td>
      <td>
        ${locked ? html`<span class="muted small"><${Icon} name="lock" /> Master admin</span>` : html`
          <div class="row">
            <select value=${role} onChange=${(e) => setRole(e.target.value)} aria-label=${`Role for ${user.email}`}>
              ${ASSIGNABLE.map((r) => html`<option key=${r} value=${r}>${ROLE_LABEL[r]}</option>`)}
            </select>
            <button class="btn btn-small btn-primary" disabled=${busy || role === user.role} onClick=${save}><${Icon} name="device-floppy" /> Save</button>
          </div>`}
      </td>
    </tr>`;
}

function UsersPanel({ me, onError, toast }) {
  const [users, setUsers] = useState(null);
  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const q = useDebounced(query.trim());

  const load = useCallback(() => api.users(q).then(setUsers).catch(onError), [q, onError]);
  useEffect(() => { load(); }, [load]);

  const change = async (user, role) => {
    try {
      const updated = await api.setRole(user.id, role);
      setUsers((list) => list.map((u) => (u.id === updated.id ? updated : u)));
      toast(`${updated.email} is now ${ROLE_LABEL[updated.role].toLowerCase()} - they will be asked to sign in again`, 'success');
    } catch (e) { onError(e); }
  };

  const shown = (users || []).filter((u) => !roleFilter || u.role === roleFilter);
  const counts = (users || []).reduce((c, u) => ({ ...c, [u.role]: (c[u.role] || 0) + 1 }), {});

  return html`
    <section class="card">
      <div class="card-title-row">
        <h2><${Icon} name="users" /> Registered users</h2>
        <div class="row">
          <input type="search" placeholder="Search e-mail…" value=${query} onInput=${(e) => setQuery(e.target.value)} />
          <select value=${roleFilter} onChange=${(e) => setRoleFilter(e.target.value)}>
            <option value="">All roles</option>
            ${Object.keys(ROLE_LABEL).map((r) => html`<option key=${r} value=${r}>${ROLE_LABEL[r]} (${counts[r] || 0})</option>`)}
          </select>
        </div>
      </div>
      ${!users && html`<p class="muted">Loading users…</p>`}
      ${users && html`
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>E-mail</th><th>Role</th><th>Registered</th><th>Assign role</th></tr></thead>
            <tbody>${shown.map((u) => html`<${UserRow} key=${u.id} user=${u} me=${me} onChange=${change} />`)}</tbody>
          </table>
        </div>
        <p class="muted small">Promoting a passenger to operator signs them out; their next login opens the operator dashboard.</p>`}
    </section>`;
}

export function AdminApp() {
  const session = useSession(['admin']);
  const [toasts, toast] = useToasts();
  const { expired } = session;
  const onError = useCallback((e) => (e.status === 401 ? expired() : toast(e.message, 'error')), [expired, toast]);

  if (!session.identity) return html`<${Loading} />`;
  return html`
    <${Header} subtitle="Master admin · user & role management"
               identity=${session.identity} onSignOut=${session.signOut}
               links=${[{ href: '/operator.html', label: 'Operator dashboard', icon: 'layout-dashboard' }]} />
    <main class="operator">
      <${UsersPanel} me=${session.identity} onError=${onError} toast=${toast} />
    </main>
    <${Toasts} toasts=${toasts} />`;
}
