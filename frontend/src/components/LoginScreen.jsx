import { useState } from 'react';
import { useGoogleLogin } from '@react-oauth/google';

function K8sLogo({ size = 40 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none">
      <circle cx="20" cy="20" r="17.5" stroke="currentColor" strokeWidth="2.5" />
      <text
        x="20" y="26"
        textAnchor="middle"
        fontSize="13"
        fontWeight="800"
        fill="currentColor"
        fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
        letterSpacing="-0.5"
      >k8</text>
    </svg>
  );
}

function initials(name) {
  return name.trim().split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
}

function avatarColor(name) {
  const colors = ['#cf3081', '#805ad5', '#2b6cb0', '#2f855a', '#dd6b20', '#b7791f'];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return colors[h % colors.length];
}

export default function LoginScreen({ onLogin, googleClientId = '' }) {
  const [newName, setNewName] = useState('');
  const [googleError, setGoogleError] = useState('');
  const users = JSON.parse(localStorage.getItem('k8s-monitor.users') || '[]');

  const googleLogin = useGoogleLogin({
    flow: 'implicit',
    onSuccess: tokenResponse => {
      fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
        headers: { Authorization: `Bearer ${tokenResponse.access_token}` },
      })
        .then(r => r.json())
        .then(profile => {
          if (profile.name) onLogin(profile.name, { email: profile.email, picture: profile.picture, google: true });
          else setGoogleError('Não foi possível obter o perfil do Google.');
        })
        .catch(() => setGoogleError('Erro ao autenticar com Google.'));
    },
    onError: () => setGoogleError('Login com Google cancelado ou com erro.'),
  });

  function handleSelect(name) { onLogin(name); }

  function handleAdd(e) {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    onLogin(name);
  }

  return (
    <div className="login-backdrop">
      <div className="login-card">
        <div className="login-logo">
          <K8sLogo size={48} />
          <div className="login-brand">
            <span className="login-brand-app">K8s Monitor</span>
          </div>
        </div>

        <h2 className="login-title">Quem é você?</h2>
        <p className="login-subtitle">Sua sessão é independente — filtros e tema ficam salvos no seu perfil.</p>

        {googleClientId && (
          <>
            <button className="btn-google" onClick={googleLogin} type="button">
              <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.14 0 5.95 1.08 8.17 2.86l6.1-6.1C34.43 3.08 29.5 1 24 1 14.82 1 7.07 6.48 3.64 14.18l7.1 5.52C12.48 13.67 17.76 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.55c0-1.64-.15-3.22-.42-4.75H24v9.01h12.42c-.54 2.88-2.17 5.32-4.62 6.96l7.1 5.52C43.11 37.6 46.1 31.55 46.1 24.55z"/><path fill="#FBBC05" d="M10.74 28.3A14.54 14.54 0 0 1 9.5 24c0-1.5.26-2.95.72-4.3L3.12 14.18A23.93 23.93 0 0 0 0 24c0 3.87.93 7.53 2.56 10.76l8.18-6.46z"/><path fill="#34A853" d="M24 47c5.5 0 10.12-1.82 13.49-4.95l-7.1-5.52c-1.97 1.32-4.49 2.1-6.39 2.1-6.24 0-11.52-4.17-13.26-9.83l-8.18 6.46C7.07 41.52 14.82 47 24 47z"/></svg>
              Entrar com Google
            </button>
            {googleError && <p className="login-google-error">{googleError}</p>}
            <div className="login-divider"><span>ou</span></div>
          </>
        )}

        {users.length > 0 && (
          <div className="login-users">
            {users.map(u => (
              <button key={u} className="login-user-btn" onClick={() => handleSelect(u)}>
                <span className="login-avatar" style={{ background: avatarColor(u) }}>
                  {initials(u)}
                </span>
                <span className="login-user-name">{u}</span>
              </button>
            ))}
          </div>
        )}

        <div className="login-divider">
          <span>{users.length > 0 ? 'ou entre com outro nome' : 'Digite seu nome para começar'}</span>
        </div>

        <form className="login-form" onSubmit={handleAdd}>
          <input
            className="login-input"
            type="text"
            placeholder="Seu nome..."
            value={newName}
            onChange={e => setNewName(e.target.value)}
            autoFocus
            maxLength={32}
          />
          <button className="login-submit" type="submit" disabled={!newName.trim()}>
            Entrar →
          </button>
        </form>
      </div>
    </div>
  );
}
