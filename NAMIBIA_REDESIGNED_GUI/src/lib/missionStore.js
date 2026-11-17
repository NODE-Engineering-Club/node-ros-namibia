import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import { DEFAULT_CRUISE_MS, toExport } from './missionFile.js';

/**
 * The saved-mission library behind the Repo screen.
 *
 * Against a backend: the routes live ON THE GUI SERVER (gui_backend
 * /api/routes, core/route_store.py), one JSON file each, so every laptop
 * connected to it sees the same list and an upload from yesterday is still
 * there. Deploying one is a command (deploy_route) that sends the server's copy
 * of the waypoints to mission_manager.
 *
 * In mock mode there is no server: the library falls back to this browser's
 * localStorage, as before, and nothing can be deployed.
 *
 * Which route is "selected" (shown in Navigate) is per browser in both modes.
 */

const KEY = 'asket.redesign.missions.v1';
const ACTIVE = 'asket.redesign.active.v1';
const listeners = new Set();

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or blocked: the library simply does not persist */
  }
  snapshot = null;
  listeners.forEach((l) => l());
}

let snapshot = null;
function getSnapshot() {
  if (!snapshot) snapshot = { local: read(KEY, []), activeId: read(ACTIVE, null) };
  return snapshot;
}
function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// -- server ------------------------------------------------------------------

/** A stored route (route_store.py) in the shape the screens use. */
export function fromServer(doc) {
  return {
    id: doc.id,
    name: doc.name,
    cruise_speed_ms: doc.cruise_speed_ms > 0 ? doc.cruise_speed_ms : DEFAULT_CRUISE_MS,
    obstacle_policy: doc.obstacle_policy || 'stop',
    sensors: doc.sensors || {},
    waypoints: doc.waypoints || [],
    source: 'json',
    file_name: doc.file_name || '',
    saved_utc_ms: doc.saved_utc_ms,
    fingerprint: doc.fingerprint,
    stored: 'server',
  };
}

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    });
  } catch {
    throw new Error('the GUI server cannot be reached');
  }
  let body = null;
  try { body = await res.json(); } catch { /* empty or not JSON */ }
  if (!res.ok) throw new Error(body?.error || `the server answered ${res.status}`);
  return body;
}

export const routesApi = {
  list: () => api('/api/routes'),
  deployed: () => api('/api/routes/deployed'),
  upload: (mission) => api('/api/routes', {
    method: 'POST', body: JSON.stringify({ ...toExport(mission), file_name: mission.file_name || '' }),
  }),
  replace: (id, mission) => api(`/api/routes/${encodeURIComponent(id)}`, {
    method: 'PUT', body: JSON.stringify({ ...toExport(mission), file_name: mission.file_name || '' }),
  }),
  remove: (id) => api(`/api/routes/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};

/**
 * @returns {{
 *   mode: 'server' | 'browser', missions, active, loading, error,
 *   save(mission): Promise<{ok, mission?, error?}>, remove(id): Promise<{ok, error?}>,
 *   setActive(id), refresh(), localOnly
 * }}
 */
export function useMissionLibrary(connection) {
  const mode = connection?.isMock ? 'browser' : 'server';
  const { local, activeId } = useSyncExternalStore(subscribe, getSnapshot);
  const [server, setServer] = useState({ missions: [], loading: mode === 'server', error: null });

  const refresh = useCallback(async () => {
    if (mode !== 'server') return;
    try {
      const body = await routesApi.list();
      setServer({ missions: (body.routes || []).filter((r) => !r.error).map(fromServer), loading: false, error: null });
    } catch (err) {
      setServer((s) => ({ ...s, loading: false, error: err.message }));
    }
  }, [mode]);

  useEffect(() => { refresh(); }, [refresh]);

  const save = useCallback(async (mission) => {
    if (mode === 'browser') {
      const list = read(KEY, []);
      const entry = { ...mission, saved_utc_ms: Date.now(), stored: 'browser' };
      const i = list.findIndex((m) => m.id === mission.id);
      if (i >= 0) list[i] = entry;
      else list.unshift(entry);
      write(KEY, list);
      return { ok: true, mission: entry };
    }
    try {
      const onServer = server.missions.some((m) => m.id === mission.id);
      const body = onServer ? await routesApi.replace(mission.id, mission) : await routesApi.upload(mission);
      await refresh();
      return { ok: true, mission: fromServer(body.route) };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  }, [mode, server.missions, refresh]);

  const remove = useCallback(async (id) => {
    if (read(ACTIVE, null) === id) write(ACTIVE, null);
    if (mode === 'browser') {
      write(KEY, read(KEY, []).filter((m) => m.id !== id));
      return { ok: true };
    }
    try {
      await routesApi.remove(id);
      await refresh();
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  }, [mode, refresh]);

  /** Browser-only copies left from before the server store: upload them. */
  const uploadLocal = useCallback(async () => {
    const errors = [];
    for (const m of read(KEY, [])) {
      try { await routesApi.upload(m); } catch (err) { errors.push(`${m.name}: ${err.message}`); }
    }
    if (!errors.length) write(KEY, []);
    await refresh();
    return errors;
  }, [refresh]);

  const setActive = useCallback((id) => write(ACTIVE, id), []);

  const missions = mode === 'server' ? server.missions : local;
  const active = missions.find((m) => m.id === activeId) || null;
  return {
    mode,
    missions,
    active,
    loading: mode === 'server' && server.loading,
    error: mode === 'server' ? server.error : null,
    save,
    remove,
    setActive,
    refresh,
    localOnly: mode === 'server' ? local : [],
    uploadLocal,
  };
}

/**
 * The route mission_manager is (or was last) following, as this server sent
 * it, with its waypoints. Refetched when the deployment changes.
 */
export function useDeployedRoute(connection, deployed) {
  const [route, setRoute] = useState(null);
  const key = deployed ? `${deployed.fingerprint}@${deployed.deployed_utc_ms}` : '';
  useEffect(() => {
    if (connection?.isMock || !key) { setRoute(null); return undefined; }
    let live = true;
    routesApi.deployed()
      .then((body) => { if (live) setRoute(body.deployed || null); })
      .catch(() => { if (live) setRoute(null); });
    return () => { live = false; };
  }, [connection, key]);
  return route;
}
