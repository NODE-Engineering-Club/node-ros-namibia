import { useState } from 'react';

import { latestCommand } from '../lib/vessel.js';
import { IconCheck, IconUnknown, IconWarn, IconX } from './Icons.jsx';

/** A frosted-glass panel: Figma ASKET/Glass/Surface + Border + Panel blur. */
export function GlassPanel({ title, icon, aside, children, flush = false, className = '', bodyClass = '', footer }) {
  return (
    <section className={`glass panel ${className}`}>
      {title && (
        <header className="panel-header">
          {icon}
          <span>{title}</span>
          {aside && <span className="aside">{aside}</span>}
        </header>
      )}
      <div className={`panel-body ${flush ? 'flush' : ''} ${bodyClass}`}>{children}</div>
      {footer}
    </section>
  );
}

export function Pill({ level = 'neutral', children, title, dot = true }) {
  return (
    <span className={`pill ${level}`} title={title}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

const BANNER_ICON = { ok: <IconCheck />, warn: <IconWarn />, alarm: <IconX />, info: <IconUnknown /> };

export function Banner({ level = 'info', title, detail, actions, icon }) {
  return (
    <div className={`banner ${level}`} role={level === 'alarm' ? 'alert' : 'status'}>
      {icon ?? BANNER_ICON[level]}
      <div>
        <strong>{title}</strong>
        {detail && <div className="detail">{detail}</div>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

/**
 * Two-step command button.
 *
 * Same safety rules as src/asket_gui (ConfirmButton + commands.py):
 *  - rule 5: a command that changes what the vessel does needs a second click;
 *  - rule 4: the button reports the SERVER's verdict (pending / confirmed /
 *    failed), never what was asked for.
 */
export function ConfirmAction({
  connection, state, command, args = {}, label, prompt, icon, variant = '', disabled = false, title,
}) {
  const [asking, setAsking] = useState(false);
  const latest = latestCommand(state, command);
  const pending = latest?.status === 'pending';

  if (asking) {
    return (
      <span className="confirm">
        <span className="confirm-prompt">{prompt || `${label}?`}</span>
        <span className="btn-row">
          <button
            type="button"
            className={`btn small ${variant || 'primary'}`}
            onClick={() => { setAsking(false); connection.command(command, args); }}
          >
            Confirm
          </button>
          <button type="button" className="btn small ghost" onClick={() => setAsking(false)}>Cancel</button>
        </span>
      </span>
    );
  }

  return (
    <span className="confirm">
      <button
        type="button"
        className={`btn ${variant}`}
        disabled={disabled || pending}
        title={title}
        onClick={() => setAsking(true)}
      >
        {icon}
        {pending ? `${label}…` : label}
      </button>
      {latest && latest.status !== 'pending' && latest.detail && (
        <span className={`confirm-result ${latest.status}`}>{latest.detail}</span>
      )}
    </span>
  );
}

export function StatusIcon({ status }) {
  if (status === 'PASS') return <span style={{ color: 'var(--ok)' }}><IconCheck /></span>;
  if (status === 'WARN') return <span style={{ color: 'var(--warn)' }}><IconWarn /></span>;
  if (status === 'FAIL') return <span style={{ color: 'var(--alarm)' }}><IconX /></span>;
  return <span style={{ color: 'var(--neutral)' }}><IconUnknown /></span>;
}

export const CHECK_PILL = {
  PASS: { level: 'ok', text: 'OK' },
  WARN: { level: 'warn', text: 'Warning' },
  FAIL: { level: 'alarm', text: 'Fail' },
  SKIPPED: { level: 'neutral', text: 'Not checked' },
};

/** Schematic course preview used by the Repo list (not for navigation). */
export function CoursePreview({ waypoints, className = 'course' }) {
  if (!waypoints?.length) return <svg className={className} />;
  const lats = waypoints.map((w) => w.lat);
  const lons = waypoints.map((w) => w.lon);
  const [minLat, maxLat, minLon, maxLon] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
  const span = Math.max(maxLat - minLat, (maxLon - minLon) * Math.cos((minLat * Math.PI) / 180), 1e-6);
  const W = 92; const H = 56; const pad = 8;
  const scale = Math.min((W - 2 * pad) / span, (H - 2 * pad) / span);
  const x = (lon) => pad + (lon - minLon) * Math.cos((minLat * Math.PI) / 180) * scale;
  const y = (lat) => H - pad - (lat - minLat) * scale;
  const pts = waypoints.map((w) => `${x(w.lon).toFixed(1)},${y(w.lat).toFixed(1)}`).join(' ');
  const first = waypoints[0];
  const last = waypoints[waypoints.length - 1];
  return (
    <svg className={`${className} well`} viewBox={`0 0 ${W} ${H}`}>
      <polyline points={pts} fill="none" stroke="var(--info)" strokeWidth="1.5" />
      <circle cx={x(first.lon)} cy={y(first.lat)} r="3" fill="none" stroke="var(--ok)" strokeWidth="1.5" />
      <rect x={x(last.lon) - 2.5} y={y(last.lat) - 2.5} width="5" height="5" fill="var(--warn)" />
    </svg>
  );
}
