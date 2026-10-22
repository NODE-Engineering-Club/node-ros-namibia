// File: cloudflare/worker-telemetry-relay.js
//
// Terminates the outbound WebSocket connection from the ASV's
// cloud_telemetry_node and broadcasts every packet to connected browser
// dashboards. Routing is by the "role" query parameter:
//   wss://<host>/api/v1/telemetry/stream?role=asv      <- the boat (sender)
//   wss://<host>/api/v1/telemetry/stream?role=browser  <- a dashboard (listener)
//
// IMPORTANT: this depends on cloud_telemetry_node's default cloud_ws_url
// (src/comms/cloud_bridge/config/cloud_bridge_params.yaml) actually
// including "?role=asv". The design note this was built from shipped a
// default URL WITHOUT that query param — with this exact Worker code, a
// connection missing "role=asv" is NOT treated as the ASV sender (see
// `isASV` below), it is added to the browser-broadcast set instead, and
// the pipeline relays nothing in either direction. Confirmed by careful
// reading, not by deploying this (no Cloudflare account/wrangler available
// in the environment this was written in) — verify role routing end to
// end with two real `wscat` connections before trusting it in the field.
//
// Needs the Durable Object binding in wrangler.toml (also provided in
// this directory) — the design note this was built from showed this
// Worker/class but never the binding config it requires to deploy at all.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/v1/telemetry/stream") {
      const id = env.TELEMETRY_RELAY.idFromName("ASKET_ASV_RELAY");
      const obj = env.TELEMETRY_RELAY.get(id);
      return obj.fetch(request);
    }

    return new Response("ASKET 2.0 Cloud Relay Operational.", { status: 200 });
  },
};

export class TelemetryRelay {
  constructor(state, env) {
    this.state = state;
    this.clients = new Set();
    this.asvSocket = null;
    this.latestPacket = null;
  }

  async fetch(request) {
    const upgradeHeader = request.headers.get("Upgrade");
    if (!upgradeHeader || upgradeHeader !== "websocket") {
      return new Response("Expected WebSocket Upgrade", { status: 426 });
    }

    const webSocketPair = new WebSocketPair();
    const [client, server] = Object.values(webSocketPair);

    const url = new URL(request.url);
    const isASV = url.searchParams.get("role") === "asv";

    server.accept();

    if (isASV) {
      // Only one ASV sender is expected per relay instance (one boat, one
      // Durable Object singleton via idFromName("ASKET_ASV_RELAY")). If a
      // second "role=asv" connection arrives (e.g. a stale reconnect that
      // hasn't dropped yet), replace the old reference rather than silently
      // ignoring the new one — the old socket's own "close" handler below
      // will still fire and null it out if it fires AFTER this reassignment,
      // which would incorrectly clear the new connection. Guard with a
      // per-connection identity check.
      this.asvSocket = server;
      server.addEventListener("message", (event) => {
        this.latestPacket = event.data;
        for (const clientWs of this.clients) {
          try {
            clientWs.send(event.data);
          } catch (err) {
            this.clients.delete(clientWs);
          }
        }
      });
      server.addEventListener("close", () => {
        if (this.asvSocket === server) {
          this.asvSocket = null;
        }
      });
    } else {
      this.clients.add(server);
      if (this.latestPacket) {
        server.send(this.latestPacket);
      }
      server.addEventListener("close", () => {
        this.clients.delete(server);
      });
    }

    return new Response(null, { status: 101, webSocket: client });
  }
}
