import { useEffect, useState } from 'react';

import { streamPayload } from '@asket/lib/connection.js';
import { readiness } from '@asket/lib/readiness.js';
import { useStore } from '@asket/lib/useStore.js';

/**
 * Everything the redesigned screens read, derived from the SAME connection
 * store and the SAME readiness rules as src/asket_gui. No second source of
 * truth: if the original cockpit says NOT READY, so does this one, for the
 * same reason.
 */

//: Mirrors src/asket_gui/src/App.jsx — the header reports what has actually
//: been received, not what the socket believes about itself.
export const NO_DATA_AFTER_MS = 6000;

//: Mirrors SUBSCRIPTIONS in src/asket_gui/src/App.jsx. Requests, not
//: entitlements: the server decides what it serves on the current link.
export const SUBSCRIPTIONS = [
  { name: 'vessel', rate_hz: 5 },
  { name: 'pico', rate_hz: 2 },
  { name: 'heading', rate_hz: 2 },
  { name: 'link', rate_hz: 1 },
  { name: 'track', rate_hz: 1 },
  { name: 'coverage', rate_hz: 1 },
  { name: 'lidar', rate_hz: 5 },
  { name: 'power', rate_hz: 1 },
  { name: 'sonar', rate_hz: 1 },
  { name: 'mission', rate_hz: 1 },
  { name: 'diagnostics', rate_hz: 0.2 },
  { name: 'plan' },
  { name: 'camera', rate_hz: 5 },
  // Masks, labels and distances on the camera frame (full link only; refused
  // elsewhere): the OAK's on-camera detector at ~10 fps, or SAM 2.
  { name: 'camera_seg', rate_hz: 5 },
];

/** Re-render on a timer so ages and "no data for N s" keep moving. */
export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function useVessel(connection) {
  const state = useStore(connection);
  const now = useNow(1000);
  const sinceMs = state.lastMessageAt ? now - state.lastMessageAt : null;
  const receiving = sinceMs !== null && sinceMs < NO_DATA_AFTER_MS;
  const p = (name) => streamPayload(state, name);
  const pico = p('pico');
  const link = p('link');
  const report = p('diagnostics');
  const ready = readiness({
    connected: state.connected,
    receiving,
    report,
    pico,
    link,
    profile: state.profile?.profile,
    nowMs: now,
  });
  return {
    state,
    now,
    sinceMs,
    receiving,
    ready,
    pico,
    link,
    report,
    vessel: p('vessel'),
    heading: p('heading'),
    power: p('power'),
    sonar: p('sonar'),
    lidar: p('lidar'),
    mission: p('mission'),
    alarms: state.alarms || [],
    isMock: connection.isMock,
    isCloud: Boolean(connection.isCloud),
    simulated: state.hello?.source?.mode === 'sim',
  };
}

/** Latest command of a given name, as the server reports it (rule 4). */
export function latestCommand(state, name) {
  return Object.values(state.commands || {})
    .filter((c) => c.name === name)
    .pop();
}

/** Preflight verdict in the words the Figma screens use. */
export function preflightVerdict(v) {
  if (!v.state.connected) return { level: 'neutral', text: 'Not connected' };
  if (!v.report) return { level: 'warn', text: 'Preflight not run' };
  if (!v.report.go) return { level: 'alarm', text: 'Preflight NO-GO' };
  return { level: 'ok', text: 'Preflight complete' };
}

export function worstAlarm(alarms) {
  if (alarms.some((a) => a.severity === 'alarm')) return 'alarm';
  if (alarms.length) return 'warn';
  return 'ok';
}
