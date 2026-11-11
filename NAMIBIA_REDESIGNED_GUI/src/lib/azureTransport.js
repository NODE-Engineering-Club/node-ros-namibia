/**
 * The cockpit, fed from Azure Blob Storage instead of the boat's WebSocket.
 *
 * On the boat, `gui_mirror_node` (src/comms/cloud_bridge) is an ordinary
 * client of gui_backend and writes what it receives to
 * `<asv>/gui/latest.json` (+ hourly history and camera snapshots). This file
 * turns that back into the messages gui_backend sends, behind an object that
 * looks like a WebSocket, and hands it to the shared, unmodified `Connection`
 * from src/asket_gui — the same seam mock mode uses ("swap the socket, not the
 * app"). Every screen, readiness rule and age label therefore works as it does
 * on the boat's own network.
 *
 * What is different, and deliberate:
 *
 * - **Read-only.** Every command is answered `failed` with the reason. A page
 *   on the public internet must not be able to drive the boat, and the
 *   ConfirmAction / ModeButtons components already show a server refusal.
 * - **Nothing is replayed as new.** A stream is delivered only when the boat
 *   produced a newer value. If the boat, its backend or the shore link go
 *   quiet, nothing arrives and the cockpit's own "Link lost: nothing received
 *   for N s" banner says so — a frozen screen that looks live is the failure
 *   this GUI is built to avoid.
 * - **Ages against Azure's clock.** `server_utc_ms` is Azure's time (the HTTP
 *   Date header), so data age includes the upload delay and a laptop with a
 *   wrong clock cannot make old data look fresh.
 */

const OPEN = 1;
const CLOSED = 3;
const READ_ONLY_REASON =
  'Read-only cloud view: commands are only accepted from the GUI on the boat network.';

/** Same shape as gui_backend's /api/tiles/info, for the hosted tile rewrites. */
export const CLOUD_TILE_INFO = {
  online: true,
  fetching: false,
  fetch_suspended_reason: '',
  profile: 'full',
  sources: [
    { id: 'basemap', label: 'Basemap', attribution: 'OpenFreeMap © OpenMapTiles Data from © OpenStreetMap contributors', max_zoom: 14 },
    { id: 'seamark', label: 'Sea marks', attribution: '© OpenSeaMap contributors (CC-BY-SA)', max_zoom: 18 },
  ],
  upstream: { reachable: null, last_ok_utc_ms: null, last_error: '', fetches: 0, not_modified: 0, failures: 0 },
  cache: { enabled: false, error: '', entries: 0, bytes: 0 },
  mbtiles: { available: false, message: 'Cloud view: online basemap only.' },
};

function encodeVideo(header, image) {
  const head = new TextEncoder().encode(JSON.stringify(header));
  const out = new Uint8Array(4 + head.length + (image ? image.byteLength : 0));
  new DataView(out.buffer).setUint32(0, head.length, false);
  out.set(head, 4);
  if (image) out.set(new Uint8Array(image), 4 + head.length);
  return out.buffer;
}

/** Azure's time, from response Date headers (1 s resolution, so +500 ms). */
class AzureClock {
  constructor() { this.samples = []; }
  observe(response) {
    const d = Date.parse(response.headers.get('Date') || '');
    if (Number.isNaN(d)) return;
    this.samples.push(d + 500 - Date.now());
    if (this.samples.length > 15) this.samples.shift();
  }
  now() {
    if (!this.samples.length) return Date.now();
    const s = [...this.samples].sort((a, b) => a - b);
    return Date.now() + s[Math.floor(s.length / 2)];
  }
}

class AzureSocket {
  constructor(cfg) {
    this.cfg = cfg;
    this.readyState = 0;
    this.onopen = this.onmessage = this.onclose = this.onerror = null;
    this.clock = cfg.clock;
    this.base = `${cfg.blob.replace(/\/+$/, '')}/${encodeURIComponent(cfg.container)}`;
    this.prefix = `${cfg.asv}/gui`;
    this.sentServer = {};   // stream -> original server_utc_ms last delivered
    this.sentJson = {};     // meta kind -> JSON last delivered
    this.seq = null;
    this.helloSent = false;
    this.lastImage = null;
    this.packet = null;
    this.slow = null;       // state.json: hello, subscriptions, trails, plan
    this.slowSeq = null;
    this.busy = false;
    this.timer = setTimeout(() => this.#poll(), 0);
  }

  url(path, extra = '') {
    const enc = path.split('/').map(encodeURIComponent).join('/');
    return `${this.base}/${enc}?${this.cfg.sas}${extra}`;
  }

  #emit(obj) {
    if (this.readyState !== OPEN || !this.onmessage) return;
    this.onmessage({ data: typeof obj === 'string' || obj instanceof ArrayBuffer ? obj : JSON.stringify(obj) });
  }

  #fail(reason) {
    if (this.readyState === CLOSED) return;
    this.readyState = CLOSED;
    clearTimeout(this.timer);
    this.closeReason = reason;
    if (this.onclose) this.onclose({ code: 4000, reason });
  }

  async #poll() {
    if (this.readyState === CLOSED) return;
    if (!this.busy) {
      this.busy = true;
      try {
        const r = await fetch(this.url(`${this.prefix}/latest.json`, `&_=${Date.now()}`), { cache: 'no-store' });
        this.clock.observe(r);
        if (r.status === 401 || r.status === 403) return this.#fail('Azure refused the access token (expired or wrong).');
        if (r.status === 404) return this.#fail(`Nothing mirrored yet under ${this.prefix}/ (is gui_mirror_node running on the boat?).`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const packet = await r.json();
        // The slow part lives in state.json and is fetched only when the boat
        // says it changed: trails, plan, hello and subscriptions are most of
        // the bytes and change rarely.
        if (packet.state_seq !== undefined && packet.state_seq !== this.slowSeq) {
          const s = await fetch(this.url(`${this.prefix}/state.json`, `&_=${packet.state_seq}`), { cache: 'no-store' });
          if (s.ok) {
            this.slow = await s.json();
            this.slowSeq = packet.state_seq;
          }
        }
        if (this.slow) {
          packet.hello = packet.hello || this.slow.hello;
          packet.subscribed = packet.subscribed || this.slow.subscribed;
          packet.stream_unavailable = { ...(this.slow.stream_unavailable || {}), ...(packet.stream_unavailable || {}) };
          packet.frames = { ...(this.slow.frames || {}), ...(packet.frames || {}) };
        }
        if (this.readyState === 0) {
          this.readyState = OPEN;
          if (this.onopen) this.onopen({});
        }
        await this.#deliver(packet);
      } catch (err) {
        // Network trouble after opening: deliver nothing. The cockpit's
        // "nothing received for N s" is the honest reading of that.
        if (this.readyState === 0) return this.#fail(`Cannot reach Azure: ${err.message}`);
      } finally {
        this.busy = false;
      }
    }
    this.timer = setTimeout(() => this.#poll(), this.cfg.pollMs);
  }

  async #deliver(p) {
    this.packet = p;
    const serverNow = Math.round(this.clock.now());
    if (!this.helloSent && p.hello) {
      this.#emit({ ...p.hello, server_utc_ms: serverNow });
      this.helloSent = true;
      if (this.pendingSubscribe) this.#answerSubscribe(this.pendingSubscribe);
    }
    if (p.seq === this.seq) return;
    this.seq = p.seq;
    const backendUp = p.mirror?.backend_connected !== false;

    for (const [name, frame] of Object.entries(p.frames || {})) {
      const t = frame.server_utc_ms ?? frame.source_utc_ms ?? 0;
      if (this.sentServer[name] !== undefined && t <= this.sentServer[name]) continue;
      this.sentServer[name] = t;
      this.#emit({ ...frame, server_utc_ms: serverNow });
    }
    for (const kind of ['profile', 'notice']) {
      const msg = p[kind];
      if (!msg) continue;
      const j = JSON.stringify(msg);
      if (j === this.sentJson[kind]) continue;
      this.sentJson[kind] = j;
      this.#emit({ ...msg, server_utc_ms: serverNow });
    }
    for (const msg of Object.values(p.stream_unavailable || {})) {
      const key = `su:${msg.stream}`;
      const j = JSON.stringify(msg);
      if (j === this.sentJson[key]) continue;
      this.sentJson[key] = j;
      this.#emit({ ...msg, server_utc_ms: serverNow });
    }
    // The current alarm list, once per new record while the boat's backend
    // is connected: it is what the boat says now, and it keeps "receiving"
    // true between position updates. Never sent when the backend is down.
    if (p.alarms && backendUp) this.#emit({ ...p.alarms, server_utc_ms: serverNow });

    await this.#camera(p);
  }

  async #camera(p) {
    const blob = p.last_image_blob;
    const header = p.camera_image_header;
    if (blob && header && blob !== this.lastImage) {
      try {
        const r = await fetch(this.url(blob), { cache: 'force-cache' });
        if (r.ok) {
          this.lastImage = blob;  // only once we have it: a failed fetch is retried next poll
          const image = await r.arrayBuffer();
          // rate_hz is what reaches the cloud (one snapshot every N s), not
          // the camera's own rate: the panel judges lateness from it.
          this.#emit(encodeVideo({ ...header, type: 'camera_frame', rate_hz: this.cfg.cameraRateHz }, image));
        }
      } catch { /* the picture simply ages */ }
    }
    if (p.camera_status) {
      const j = JSON.stringify(p.camera_status);
      if (j !== this.sentJson.camera_status) {
        this.sentJson.camera_status = j;
        this.#emit(encodeVideo({ ...p.camera_status, type: 'camera_status' }));
      }
    }
  }

  #answerSubscribe(streams) {
    const mirrored = Object.fromEntries((this.packet?.subscribed?.streams || []).map((s) => [s.name, s]));
    this.#emit({
      type: 'subscribed',
      server_utc_ms: Math.round(this.clock.now()),
      profile: this.packet?.profile ? {
        profile: this.packet.profile.profile, manual: this.packet.profile.manual, reason: this.packet.profile.reason,
      } : (this.packet?.subscribed?.profile ?? { profile: 'full', manual: false, reason: 'cloud' }),
      streams: streams.map((s) => mirrored[s.name]
        ? { ...mirrored[s.name], name: s.name }
        : { name: s.name, granted: false, rate_hz: 0, detail: '', reason: 'not mirrored to the cloud' }),
    });
  }

  send(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    // Answer asynchronously, as a real socket would.
    setTimeout(() => {
      if (msg.type === 'subscribe') {
        if (this.helloSent) this.#answerSubscribe(msg.streams || []);
        else this.pendingSubscribe = msg.streams || [];
      } else if (msg.type === 'command') {
        this.#emit({
          type: 'command_result', id: msg.id, name: msg.name, status: 'failed',
          detail: READ_ONLY_REASON, server_utc_ms: Math.round(this.clock.now()),
        });
      }
      // pong, unsubscribe, set_profile: nothing to do in a read-only replay.
    }, 0);
  }

  close() { this.#fail('closed'); }
}

/**
 * A Connection whose transport reads Azure. `Connection` is passed in so
 * this file does not depend on the shared module's path.
 */
export function createAzureConnection(Connection, cfg) {
  const full = { pollMs: 1000, cameraRateHz: 1 / 30, ...cfg, clock: new AzureClock() };
  const connection = new Connection(`azure://${full.asv}`, {
    transport: () => new AzureSocket(full),
    tileInfo: () => Promise.resolve(CLOUD_TILE_INFO),
  });
  connection.isCloud = true;
  // Public tile servers, fetched directly by the browser (all three send
  // Access-Control-Allow-Origin: *). gui_backend proxies the same ones on the
  // boat; a static host such as the Azure website has no proxy.
  connection.tileUrls = {
    basemap: 'https://tiles.openfreemap.org/planet/latest/{z}/{x}/{y}.pbf',
    seamark: 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
    glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  };
  connection.cloud = { asv: full.asv, container: full.container, blob: full.blob };
  return connection;
}
