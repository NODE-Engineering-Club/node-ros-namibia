import { useState } from 'react';

import { latestCommand } from '../lib/vessel.js';
import {
  IconCheck, IconChevron, IconSearch, IconShield, IconUnknown, IconWarn, IconX,
} from './Icons.jsx';

/** A frosted-glass panel: Figma ASKET/Glass/Surface + Border + Panel blur. */
export function GlassPanel({
  title, icon, aside, children, flush = false, className = '', bodyClass = '', footer, notes,
}) {
  return (
    <section className={`glass panel ${className}`}>
      {title && (
        <header className="panel-header">
          {icon}
          <span>{title}</span>
          {aside && <span className="aside">{aside}</span>}
        </header>
      )}
      {notes}
      <div className={`panel-body ${flush ? 'flush' : ''} ${bodyClass}`}>{children}</div>
      {footer}
    </section>
  );
}

/**
 * Subscription reasons and silent sources for the streams a panel shows
 * (lib/streamNotes.js). Rendered under the panel header, never folded away:
 * "I am not being sent it" and "nothing is producing it" must not look like
 * "nothing is happening".
 */
export function StreamNotes({ notes }) {
  if (!notes?.length) return null;
  return (
    <div className="stream-notes" role="status">
      {notes.map((n) => (
        <div key={n.stream} className={`stream-note ${n.kind}`}>
          <strong>{n.stream}: {n.label}</strong>
          {n.reason ? <span> — {n.reason}</span> : null}
        </div>
      ))}
    </div>
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

const BANNER_ICON = { ok: <IconShield />, warn: <IconShield />, alarm: <IconX />, info: <IconUnknown /> };

/**
 * Figma banners come in two shapes: two lines (title over detail, Preflight)
 * and one line (`inline`: bold title, then the detail, Alerts / Repo).
 */
export function Banner({ level = 'info', title, detail, actions, icon, inline = false }) {
  return (
    <div className={`banner ${level} ${inline ? 'inline' : ''}`} role={level === 'alarm' ? 'alert' : 'status'}>
      {icon ?? BANNER_ICON[level]}
      <div className="banner-text">
        <strong>{title}</strong>
        {detail && <span className="detail">{detail}</span>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

/** Figma search field: glass input with a magnifier. */
export function SearchInput({ value, onChange, placeholder, className = '' }) {
  return (
    <label className={`search ${className}`}>
      <IconSearch />
      <input className="input" placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** Figma dropdown: "Label: Value ⌄" on glass. A native select underneath. */
export function Select({ label, value, onChange, options, icon }) {
  const current = options.find((o) => o.value === value);
  return (
    <label className="select">
      {icon}
      <span className="select-text">{label ? `${label}: ` : ''}{current?.label}</span>
      <IconChevron />
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

/**
 * Two-step command button.
 *
 * Same safety rules as src/asket_gui (ConfirmButton + commands.py):
 *  - rule 5: a command that changes what the vessel does needs a second click;
 *  - rule 4: the button reports the SERVER's verdict (pending / confirmed /
 *    failed), never what was asked for.
 *
 * `failuresOnly`: where the confirmed outcome is already shown elsewhere on
 * screen (e.g. the REC pill), only a failure is printed under the button.
 */
export function ConfirmAction({
  connection, state, command, args = {}, label, prompt, icon, variant = '', disabled = false, title,
  failuresOnly = false,
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
      {latest && latest.status !== 'pending' && latest.detail
        && (!failuresOnly || latest.status === 'failed') && (
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
  const W = 150; const H = 56; const pad = 10;
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
