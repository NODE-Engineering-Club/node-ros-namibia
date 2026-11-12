import { useCallback, useSyncExternalStore } from 'react';

/**
 * The saved-mission library behind the Repo screen.
 *
 * Planned routes live in this browser (localStorage) and are exported as
 * .json. The backend's `mission` stream is something else — the RECORDINGS on
 * the vessel's disk — and the Repo screen shows both, labelled apart.
 *
 * When gui_backend gains a route store (see README, "Not wired yet"), this is
 * the one file to swap.
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
  if (!snapshot) snapshot = { missions: read(KEY, []), activeId: read(ACTIVE, null) };
  return snapshot;
}
function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useMissionLibrary() {
  const { missions, activeId } = useSyncExternalStore(subscribe, getSnapshot);

  const save = useCallback((mission) => {
    const list = read(KEY, []);
    const entry = { ...mission, saved_utc_ms: Date.now() };
    const i = list.findIndex((m) => m.id === mission.id);
    if (i >= 0) list[i] = entry;
    else list.unshift(entry);
    write(KEY, list);
    return entry;
  }, []);

  const remove = useCallback((id) => {
    write(KEY, read(KEY, []).filter((m) => m.id !== id));
    if (read(ACTIVE, null) === id) write(ACTIVE, null);
  }, []);

  const setActive = useCallback((id) => write(ACTIVE, id), []);

  const active = missions.find((m) => m.id === activeId) || null;
  return { missions, active, save, remove, setActive };
}
