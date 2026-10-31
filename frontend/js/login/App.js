import { api } from '../api.js';
import { html, useState, useEffect } from '../lib.js';
import { Icon, Logo } from '../components/common.js';

const TITLES = {
  login: ['Sign in', 'One login for passengers, operators and administrators'],
  register: ['Create account', 'New accounts start as passengers'],
  forgot: ['Forgot password', 'Enter your e-mail and we will send you a 6-digit verification code'],
  reset: ['Reset password', 'Enter the code from your e-mail and choose a new password'],
};

function Field({ label, icon, ...props }) {
  return html`<label class="field"><span>${label}</span>
    <span class="input-icon"><${Icon} name=${icon} /><input required ...${props} /></span></label>`;
}

export function LoginApp() {
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(true);

  // Already signed in? Go straight to the dashboard of the user's role.
  useEffect(() => {
    api.me().then((me) => window.location.replace(me.home)).catch(() => setChecking(false));
  }, []);

  const go = (next) => { setMode(next); setError(null); setPassword(''); if (next !== 'reset') setNotice(null); };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === 'login' || mode === 'register') {
        const session = mode === 'login' ? await api.login(email, password) : await api.register(email, password);
        window.location.replace(session.home);  // role-based routing
        return;
      }
      if (mode === 'forgot') {
        const res = await api.forgotPassword(email);
        setNotice(res.detail);
        setMode('reset');
      } else if (mode === 'reset') {
        await api.resetPassword(email, code.trim(), password);
        setNotice('Password changed. Sign in with your new password.');
        setCode('');
        setPassword('');
        setMode('login');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (checking) return html`<div class="center muted">Loading…</div>`;
  const [title, subtitle] = TITLES[mode];
  const submitLabel = { login: 'Sign in', register: 'Create account', forgot: 'Send code', reset: 'Set new password' }[mode];

  return html`
    <main class="auth-page">
      <form class="card auth-card" onSubmit=${submit}>
        <${Logo} className="auth-logo" />
        <h1>${title}</h1>
        <p class="muted">${subtitle}</p>
        ${notice && html`<p class="notice" role="status"><${Icon} name="info-circle" /> ${notice}</p>`}

        <${Field} label="E-mail" icon="mail" type="email" autocomplete="email" value=${email}
                  readonly=${mode === 'reset'} onInput=${(e) => setEmail(e.target.value)} />
        ${mode === 'reset' && html`
          <${Field} label="Verification code" icon="shield-check" inputmode="numeric" pattern="[0-9]{6}" maxlength="6"
                    autocomplete="one-time-code" placeholder="6 digits" value=${code}
                    onInput=${(e) => setCode(e.target.value.replace(/\D/g, ''))} />`}
        ${mode !== 'forgot' && html`
          <${Field} label=${mode === 'reset' ? 'New password' : 'Password'} icon="lock" type="password"
                    autocomplete=${mode === 'login' ? 'current-password' : 'new-password'}
                    minlength=${mode === 'login' ? undefined : 8} value=${password} onInput=${(e) => setPassword(e.target.value)} />`}

        ${error && html`<p class="error" role="alert"><${Icon} name="alert-circle" /> ${error}</p>`}
        <button class="btn btn-primary btn-block" disabled=${busy}>${busy ? 'Please wait…' : submitLabel}</button>

        <div class="auth-links">
          ${mode === 'login' && html`
            <button type="button" class="link" onClick=${() => go('forgot')}>Forgot password?</button>
            <button type="button" class="link" onClick=${() => go('register')}>Create an account</button>`}
          ${mode === 'register' && html`<button type="button" class="link" onClick=${() => go('login')}>Already registered? Sign in</button>`}
          ${mode === 'forgot' && html`<button type="button" class="link" onClick=${() => go('login')}>Back to sign in</button>`}
          ${mode === 'reset' && html`
            <button type="button" class="link" onClick=${() => go('forgot')}>Send a new code</button>
            <button type="button" class="link" onClick=${() => go('login')}>Back to sign in</button>`}
        </div>
      </form>
    </main>`;
}
