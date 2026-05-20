import { useState } from 'react';

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

export default function LoginScreen({ onLogin }) {
  const [newName, setNewName] = useState('');
  const users = JSON.parse(localStorage.getItem('k8s-monitor.users') || '[]');

  function handleSelect(name) {
    onLogin(name);
  }

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

        {users.length > 0 && (
          <div className="login-users">
            {users.map(u => (
              <button key={u} className="login-user-btn" onClick={() => handleSelect(u)}>
                <span
                  className="login-avatar"
                  style={{ background: avatarColor(u) }}
                >
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
