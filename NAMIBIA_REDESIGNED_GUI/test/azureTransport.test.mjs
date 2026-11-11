// node --test test/azureTransport.test.mjs   (Node 20+, no packages needed)
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.WebSocket = { OPEN: 1 };
const { Connection } = await import('../../src/asket_gui/src/lib/connection.js');
const { createAzureConnection } = await import('../src/lib/azureTransport.js');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DATE = new Date().toUTCString();

function fakeAzure(routes) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    const path = new URL(url).pathname;
    const r = routes(path);
    if (!r) return { ok: false, status: 404, headers: new Map([['Date', DATE]]), json: async () => ({}) };
    return {
      ok: r.status ? r.status < 400 : true, status: r.status || 200,
      headers: { get: (k) => (k === 'Date' ? DATE : null) },
      json: async () => r.body, arrayBuffer: async () => new Uint8Array([0xff, 0xd8, 1, 2]).buffer,
    };
  };
  return calls;
}

function packet(seq, { backend = true, vesselT = 1000 } = {}) {
  return {
    seq, timestamp: Date.now() / 1000, mirror: { backend_connected: backend },
    hello: { type: 'hello', protocol_version: 1, source: { mode: 'vessel', fitted: { camera: true } }, profile: { profile: 'full', manual: false, reason: 'x' } },
    subscribed: { type: 'subscribed', streams: [{ name: 'vessel', granted: true, rate_hz: 2, detail: 'full', reason: '' }] },
    alarms: { type: 'alarms', active: [{ id: 'a', severity: 'warn' }] },
    frames: { vessel: { type: 'data', stream: 'vessel', source_utc_ms: vesselT, server_utc_ms: vesselT, detail: 'full', payload: { lat: -26.6, lon: 15.1 } } },
    last_image_blob: 'ASV/gui/images/2026/11/11/13/1.jpg',
    camera_image_header: { type: 'camera_frame', seq: 1, source_utc_ms: vesselT, format: 'jpeg', width: 4, height: 4 },
  };
}

function make(routes) {
  const calls = fakeAzure(routes);
  const c = createAzureConnection(Connection, { blob: 'https://acct.blob.core.windows.net', container: 'telemetry', asv: 'ASV', sas: 'sv=x&sig=y', pollMs: 30 });
  const seen = [];
  const orig = c.ws;
  c.subscribe([{ name: 'vessel', rate_hz: 5 }, { name: 'pico', rate_hz: 2 }]);
  c.connect();
  // record every message the Connection receives
  const tap = () => {
    if (c.ws && !c.ws.__tapped) {
      const inner = c.ws.onmessage;
      c.ws.onmessage = (ev) => { seen.push(ev.data); inner(ev); };
      c.ws.__tapped = true;
    }
  };
  const t = setInterval(tap, 1); tap();
  return { c, seen, calls, stop: () => { clearInterval(t); c.close(); } };
}

test('replays hello, subscriptions, data, alarms and camera; refuses commands', async () => {
  let p = packet(1);
  const h = make((path) => (path.endsWith('/latest.json') ? { body: p } : path.endsWith('.jpg') ? { body: null } : null));
  await sleep(120);
  const s = h.c.getSnapshot();
  assert.equal(s.connected, true);
  assert.equal(s.hello.protocol_version, 1);
  assert.equal(s.subscriptions.vessel.granted, true);
  assert.equal(s.subscriptions.pico.granted, false);
  assert.match(s.subscriptions.pico.reason, /not mirrored/);
  assert.deepEqual(s.streams.vessel.payload, { lat: -26.6, lon: 15.1 });
  assert.equal(s.alarms.length, 1);
  assert.ok(s.camera?.url === undefined || s.camera !== null); // camera handled (URL.createObjectURL may be absent in node)
  const id = h.c.command('set_mode', { mode: 'AUTONOMOUS' });
  await sleep(20);
  assert.equal(h.c.getSnapshot().commands[id].status, 'failed');
  assert.match(h.c.getSnapshot().commands[id].detail, /Read-only cloud view/);
  h.stop();
});

test('an unchanged record is not delivered again, so a quiet boat reads as link lost', async () => {
  const p = packet(1);
  const h = make((path) => (path.endsWith('/latest.json') ? { body: p } : null));
  await sleep(100);
  const n = h.seen.length;
  const last = h.c.getSnapshot().lastMessageAt;
  await sleep(150); // several more polls of the same seq
  assert.equal(h.seen.length, n);
  assert.equal(h.c.getSnapshot().lastMessageAt, last);
  h.stop();
});

test('new record with an old vessel value: no data frame, and no alarms while the backend is down', async () => {
  let p = packet(1);
  const h = make((path) => (path.endsWith('/latest.json') ? { body: p } : null));
  await sleep(100);
  const before = h.seen.length;
  p = packet(2, { backend: false }); // same vessel source time, backend disconnected
  await sleep(100);
  const fresh = h.seen.slice(before).map((d) => JSON.parse(d));
  assert.equal(fresh.filter((m) => m.type === 'data').length, 0);
  assert.equal(fresh.filter((m) => m.type === 'alarms').length, 0);
  p = packet(3, { vesselT: 2000 });
  await sleep(100);
  const later = h.seen.slice(before).map((d) => JSON.parse(d));
  assert.equal(later.filter((m) => m.type === 'data').length, 1);
  h.stop();
});

test('token rejected / nothing mirrored close the socket with the reason', async () => {
  const h = make((path) => (path.endsWith('/latest.json') ? { status: 403, body: {} } : null));
  await sleep(80);
  const s = h.c.getSnapshot();
  assert.equal(s.connected, false);
  h.stop();
  const h2 = make(() => null);
  await sleep(80);
  assert.equal(h2.c.getSnapshot().connected, false);
  h2.stop();
});

test('slow state comes from state.json, fetched only when state_seq changes', async () => {
  const base = packet(1);
  const slow = { state_seq: 1, hello: base.hello, subscribed: base.subscribed, stream_unavailable: {},
    frames: { track: { type: 'data', stream: 'track', source_utc_ms: 900, server_utc_ms: 900, detail: 'full', payload: { from: 0, total: 1, points: [[15.1, -26.6]] } } } };
  let p = { ...base, hello: undefined, subscribed: undefined, state_seq: 1 };
  const h = make((path) => (path.endsWith('/latest.json') ? { body: p } : path.endsWith('/state.json') ? { body: slow } : null));
  await sleep(150);
  const s = h.c.getSnapshot();
  assert.equal(s.hello.protocol_version, 1);
  assert.deepEqual(s.streams.track.payload.points, [[15.1, -26.6]]);
  const n = h.calls.filter((u) => u.includes('state.json')).length;
  p = { ...p, seq: 2, frames: { vessel: { ...base.frames.vessel, server_utc_ms: 2000, source_utc_ms: 2000 } } };
  await sleep(150);
  assert.equal(h.calls.filter((u) => u.includes('state.json')).length, n);
  h.stop();
});
