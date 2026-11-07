import { worstAlarm } from '../lib/vessel.js';
import {
  Emblem, IconAlerts, IconMission, IconNavigate, IconPreflight, IconRepo,
} from './Icons.jsx';
import { Pill } from './ui.jsx';

export const ROUTES = [
  { id: 'alerts', label: 'Alerts', Icon: IconAlerts },
  { id: 'preflight', label: 'Preflight', Icon: IconPreflight },
  { id: 'navigate', label: 'Navigate', Icon: IconNavigate },
  { id: 'mission', label: 'Mission', Icon: IconMission },
  { id: 'repo', label: 'Repo', Icon: IconRepo },
];

export function Background() {
  return (
    <div className="app-bg" aria-hidden="true">
      <div className="pattern" />
      <div className="overlay" />
    </div>
  );
}

export function Sidebar({ route, onNavigate, alarms }) {
  const level = worstAlarm(alarms);
  return (
    <aside className="sidebar">
      <Emblem />
      <nav aria-label="Workspaces">
        {ROUTES.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            className={`nav-item ${route === id ? 'active' : ''}`}
            aria-current={route === id ? 'page' : undefined}
            onClick={() => onNavigate(id)}
          >
            {id === 'alerts' && alarms.length > 0 && (
              <span className={`nav-badge ${level}`}>{alarms.length}</span>
            )}
            <Icon />
            {label}
          </button>
        ))}
      </nav>
      <span className="vessel-label">ASKET</span>
    </aside>
  );
}

export function WorkspaceHeader({ title, crumb, children }) {
  return (
    <header className="workspace-header">
      <div>
        {crumb && <div className="crumb">{crumb}</div>}
        <h1>{title}</h1>
      </div>
      <div className="spacer" />
      {children}
    </header>
  );
}

/** Readiness, link state and data provenance — always visible. */
export function SystemFooter({ v, connection, children }) {
  const link = v.receiving
    ? 'Receiving'
    : v.state.connected
      ? `No data for ${Math.round((v.sinceMs ?? 0) / 1000)} s`
      : v.state.connecting ? 'Reconnecting…' : 'Offline';
  return (
    <footer className="system-footer">
      <span>NODE ASV Control · redesign</span>
      <span>ROS 2 Humble</span>
      <span className="spacer" />
      {children}
      <Pill level={v.receiving ? 'ok' : 'alarm'}>{link}</Pill>
      {v.simulated && <Pill level="mock" title="Every value is simulated by the backend.">Simulation</Pill>}
      {connection.isMock && <Pill level="mock" title="No backend, no vessel: generated in this browser.">Mock data — browser only</Pill>}
      <Pill level={v.ready.level === 'ok' ? 'ok' : v.ready.level}>{v.ready.text}</Pill>
    </footer>
  );
}

export function Splash({ onDone }) {
  return (
    <div className="splash" onClick={onDone} onAnimationEnd={onDone} role="presentation">
      <Emblem />
      <h1>NODE</h1>
      <p>Engineering Club</p>
    </div>
  );
}
