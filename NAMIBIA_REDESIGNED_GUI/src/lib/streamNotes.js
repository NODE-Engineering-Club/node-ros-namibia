/**
 * Why a panel may be showing less than it should (developer guide 2.2,
 * points 2 and 3). Two different faults, said in different words:
 *
 *  - "not sent" / "reduced": the server refused or cut back a subscription,
 *    and `subscribed` carried a `reason`. Nothing is wrong at the vessel; the
 *    link profile decided. Read from state.subscriptions, which the shared
 *    Connection already keeps.
 *  - "source silent": the subscription was granted, and ~5 s later the
 *    server sent `stream_unavailable` because nothing produced the data. The
 *    node that publishes it may not be running.
 *
 * The shared Connection (src/asket_gui/src/lib/connection.js) ignores
 * `stream_unavailable` frames, and this folder may not edit it. So the
 * redesign taps the socket instead: `tapStreamUnavailable` wraps the
 * connection's own transport factory, before `connect()`, and records those
 * frames on the side. The Connection still sees every frame, unchanged.
 */

const registries = new WeakMap();

function registry(connection) {
  let entries = registries.get(connection);
  if (!entries) {
    entries = new Map();
    registries.set(connection, entries);
  }
  return entries;
}

function record(connection, raw) {
  if (typeof raw !== 'string' || !raw.includes('stream_unavailable')) return;
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    return;
  }
  if (message.type !== 'stream_unavailable' || !message.stream) return;
  registry(connection).set(message.stream, { at: Date.now(), reason: message.reason || '' });
}

/**
 * Install the tap. Call once, before `connection.connect()`.
 *
 * A real WebSocket gets an extra 'message' listener (the Connection uses
 * `onmessage`; both fire). The mock transport has no addEventListener, only
 * an `onmessage` property, so that property is wrapped instead.
 *
 * `beforeOpen` lets the mock-only test rig refuse to open a socket (a backend
 * that is down); it is never set against a real backend.
 */
export function tapStreamUnavailable(connection, { beforeOpen } = {}) {
  const open = connection.openTransport;
  connection.openTransport = (url) => {
    if (beforeOpen) beforeOpen();
    const ws = open(url);
    if (typeof ws.addEventListener === 'function') {
      ws.addEventListener('message', (event) => record(connection, event.data));
    } else {
      let handler = ws.onmessage;
      const tapped = (event) => {
        record(connection, event.data);
        if (handler) handler(event);
      };
      Object.defineProperty(ws, 'onmessage', {
        configurable: true,
        get: () => (handler ? tapped : null),
        set: (fn) => { handler = fn; },
      });
    }
    return ws;
  };
}

/** Mock-only: record a silent stream as the backend would. */
export function markSilent(connection, stream, reason) {
  registry(connection).set(stream, { at: Date.now(), reason });
}

export function clearSilent(connection, stream) {
  registry(connection).delete(stream);
}

/** When data last arrived for a stream (the camera lives outside `streams`). */
function lastArrival(state, stream) {
  if (stream === 'camera') return state.camera?.statusAt ?? null;
  return state.streams?.[stream]?.receivedAt ?? null;
}

/**
 * The note for one stream, or null when there is nothing to say.
 * A frame that arrives after a `stream_unavailable` disproves it.
 */
export function streamNote(connection, state, stream) {
  const sub = state.subscriptions?.[stream];
  if (sub && sub.granted === false) {
    return {
      stream, kind: 'refused', level: 'neutral', label: 'not sent',
      reason: sub.reason || 'refused by the server',
    };
  }
  const silent = registry(connection).get(stream);
  if (silent && !(lastArrival(state, stream) > silent.at)) {
    return { stream, kind: 'silent', level: 'warn', label: 'source silent', reason: silent.reason };
  }
  if (sub?.reason) {
    return {
      stream, kind: 'reduced', level: 'warn',
      label: sub.rate_hz ? `reduced to ${sub.rate_hz} Hz` : 'reduced',
      reason: sub.reason,
    };
  }
  return null;
}

export function streamNotes(connection, state, streams) {
  return streams.map((s) => streamNote(connection, state, s)).filter(Boolean);
}
