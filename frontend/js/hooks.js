import { api } from './api.js';
import { useState, useEffect, useRef, useCallback } from './lib.js';

const LOGIN_PAGE = '/';

/**
 * Guards a dashboard page. Signed out -> unified login screen; signed in with a
 * role that does not belong on this page -> that role's own dashboard.
 * Returns { identity, signOut, expired } once access is confirmed (identity null while checking).
 */
export function useSession(allowedRoles) {
  const [identity, setIdentity] = useState(null);
  const rolesKey = allowedRoles.join(',');

  useEffect(() => {
    api.me()
      .then((me) => {
        if (rolesKey.split(',').includes(me.role)) setIdentity(me);
        else window.location.replace(me.home);
      })
      .catch(() => window.location.replace(LOGIN_PAGE));
  }, [rolesKey]);

  const signOut = useCallback(async () => {
    try { await api.logout(); } finally { window.location.replace(LOGIN_PAGE); }
  }, []);
  const expired = useCallback(() => window.location.replace(LOGIN_PAGE), []);

  return { identity, signOut, expired };
}

/**
 * Live feed over Server-Sent Events with REST polling as fallback.
 * handlers: { snapshot(data), buses(list), routes(list), report(r), alert(a) } - only the
 * events of the given role's stream are delivered.
 */
export function useLiveStream(role, handlers, poll) {
  const [connected, setConnected] = useState(false);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const source = new EventSource(api.streamUrl(role));
    let timer = null;
    const call = (name) => (e) => handlersRef.current[name]?.(JSON.parse(e.data));
    const startPolling = () => { if (!timer && poll) timer = setInterval(() => poll().catch(() => {}), 4000); };
    const stopPolling = () => { clearInterval(timer); timer = null; };

    source.addEventListener('open', () => { setConnected(true); stopPolling(); });
    source.addEventListener('error', () => { setConnected(false); startPolling(); });
    ['snapshot', 'buses', 'routes', 'report', 'alert'].forEach((name) => source.addEventListener(name, call(name)));
    return () => { source.close(); stopPolling(); };
  }, [role, poll]);

  return connected;
}

/** Debounced value (search-as-you-type). */
export function useDebounced(value, delay = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

/** Transient notification messages. */
export function useToasts() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((message, kind = 'info') => {
    const id = Math.random();
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return [toasts, push];
}
