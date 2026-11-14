import { createContext, useContext } from 'react';

import { worstAlarm } from '../lib/vessel.js';
import {
  Emblem, IconAlerts, IconMission, IconNavigate, IconPreflight, IconRepo,
} from './Icons.jsx';
import { Pill } from './ui.jsx';
import { version } from '../../package.json';

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
      {/* pattern.jpg is the composed Figma background (navy overlay
          already baked in), so nothing is drawn over it. */}
      <div className="pattern" />
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

/**
 * What every workspace header shows next to its own pills: the system pills
 * (link state, data provenance, readiness). App provides `{ v, connection }`.
 */
export const ShellContext = createContext(null);

/**
 * Figma header: the screen title alone on the left, the pills on the right.
 * (`crumb` is accepted and ignored: the Figma has no breadcrumb line.)
 */
export function WorkspaceHeader({ title, children }) {
  const shell = useContext(ShellContext);
  return (
    <header className="workspace-header">
      <h1>{title}</h1>
      <div className="spacer" />
      <div className="header-pills">
        {children}
        {shell && <SystemPills v={shell.v} connection={shell.connection} />}
      </div>
    </header>
  );
}

export function SystemFooter() {
  return (
    <footer className="system-footer">
      <span>NODE ASV Control v{version}</span>
      <span>·</span>
      <span>ROS2 Humble</span>
    </footer>
  );
}

/** Readiness, link state and data provenance — always visible, in the header. */
function SystemPills({ v, connection }) {
  const link = v.receiving
    ? 'Receiving'
    : v.state.connected
      ? `No data for ${Math.round((v.sinceMs ?? 0) / 1000)} s`
      : v.state.connecting ? 'Reconnecting…' : 'Offline';
  return (
    <>
      <Pill level={v.receiving ? 'ok' : 'alarm'}>{link}</Pill>
      {v.simulated && <Pill level="mock" title="Every value is simulated by the backend.">Simulation</Pill>}
      {connection.isMock && (
        <Pill level="mock" title="No backend, no vessel: generated in this browser. Ctrl+Shift+D opens the dev panel.">
          Mock data — browser only
        </Pill>
      )}
      <Pill level={v.ready.level === 'ok' ? 'ok' : v.ready.level}>{v.ready.text}</Pill>
    </>
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
