# Azure telemetry history and dashboard

The boat writes every telemetry packet to an Azure Storage account. A static
web page, served from the same account, shows it live and replays any stored
hour. No server or VM, no Azure CLI. The only secret is the storage
account's connection string, and it stays on the Jetson.

```
Jetson: cloud_telemetry_node ──► disk spool (/data/cloud_spool) ──► Azure Blob Storage, container "telemetry"
        (GPS, sonar 3D, camera,       survives link drops,            ASKET_02/latest.json
         SAM2, hazards, signal)       uploads the backlog later       ASKET_02/telemetry/YYYY/MM/DD/HH.jsonl
                                                                      ASKET_02/images/YYYY/MM/DD/HH/<ms>.jpg
Browser: static website ($web/index.html) ──read-only SAS token──► the blobs above
```

## What gets stored

One JSON line per packet, `azure_record_rate_hz` per second (default 1):

| Key | What | From |
|---|---|---|
| `nav` | lat/lon/alt, heading, speed, course, `rtk_mode`, `sats`, `hdop` | `/gps_driver/gps_raw`, `/odometry/filtered`, MAVROS `gps_vel` and `gps1/raw` |
| `sonar3d` | sonar link and settings, plus the strongest 60 returns of the newest ping and that ping's min/median/max range | `omniscan_bridge`: `/sonar/status`, `/sonar/points` |
| `sam2_polygons` | segmentation outlines, normalised to the image | `/perception/sam2_masks` |
| `lidar_clusters` | tracked hazards with lat/lon | `/obstacles/fused_tracked` |
| `image_blob` | path of a camera snapshot (one every `azure_image_interval_s`, default 5 s) | `/oak/rgb/image_raw/compressed` or `/camera/front/image_raw` |
| `link` | Jetson Wi-Fi (via `iw`), upload backlog and state, WebSocket state | the node itself |
| `health` | battery, Jetson temperature | `/battery/state`, thermal zones |

Hours are in UTC. A topic with no publisher leaves its field empty, and
packets are recorded with or without a GPS fix.

Expect about 2–3 KB per packet: roughly 200–250 MB a day at 1 Hz, plus about
0.5 GB a day of snapshots at one every 5 s. Billing is mostly write
transactions, about two every 2 s.

## One-time setup

1. **Put the connection string on the Jetson, not in git and not in chat.**
   Portal → Storage account → Security + networking → Access keys →
   Connection string. In a real terminal:

   ```bash
   mkdir -p ~/.config/asket && nano ~/.config/asket/azure_storage_connection_string   # paste, save
   chmod 600 ~/.config/asket/azure_storage_connection_string
   # where the boat container reads it (asket.service mounts /srv/asket/data as /data):
   sudo mkdir -p /srv/asket/data/secrets
   sudo cp ~/.config/asket/azure_storage_connection_string /srv/asket/data/secrets/
   sudo chmod 600 /srv/asket/data/secrets/azure_storage_connection_string
   ```

2. **Provision and publish the dashboard** (anywhere with Python and the file
   above):

   ```bash
   pip install azure-storage-blob==12.23.1
   python3 cloud/azure/provision.py
   ```

   This creates the private `telemetry` container, adds a read-only CORS rule,
   turns on the static website, uploads `web/index.html`, and prints a
   **viewer link**. The link carries a read+list token for `telemetry`, valid
   180 days. Anyone holding the link can read the telemetry, so share it
   only with the team.
   - New token, nothing else changed: `--print-sas-only`.
   - Revoke every link: rotate the account key in the portal, then update
     the connection string files.

3. **Rebuild the boat image.** The Containerfile now installs
   `azure-storage-blob` and `iw`. Then start the stack as usual with
   `enable_cloud_bridge:=true`.

## Checking it on the boat

```bash
ros2 topic echo /cloud_bridge/status --once   # "azure": connected, backlog_kb, last_error
ls /data/cloud_spool/telemetry/                # local copy of everything recorded
```

`backlog_kb` rising while `connected` is false means the shore link is down.
That's normal; it uploads when the link returns. The dashboard header
shows the age of the newest packet.

## Parameters (`cloud_bridge_params.yaml`)

| Parameter | Default | |
|---|---|---|
| `azure_enabled` | `true` | Off automatically when no connection string is found |
| `azure_connection_string_file` | `/data/secrets/azure_storage_connection_string` | `AZURE_STORAGE_CONNECTION_STRING` overrides |
| `azure_record_rate_hz` | `1.0` | Packets per second stored |
| `azure_image_interval_s` | `5.0` | `0` = no snapshots |
| `azure_upload_interval_s` | `2.0` | How often the spool is pushed |
| `azure_spool_dir` | `/data/cloud_spool` | Local copy; telemetry is kept, images are deleted once uploaded unless `azure_keep_local_images` |
| `sonar_cloud_points` | `60` | Sonar returns per ping kept |
| `cloud_ws_url` | `""` | Old live relay (Cloudflare), off unless set |

## The cockpit in the cloud (NAMIBIA_REDESIGNED_GUI)

The team's redesigned cockpit can run from anywhere, read-only, on the same
storage account:

```
boat: gui_backend ──ws──► gui_mirror_node ──► Azure  ASKET_02/gui/latest.json          (every 1 s)
      (unchanged)         read-only client          ASKET_02/gui/telemetry/…/HH.jsonl  (changes, every 5 s)
                                                    ASKET_02/gui/images/…jpg           (one every 5 s)
browser: NAMIBIA_REDESIGNED_GUI cloud build ──► src/lib/azureTransport.js ──► the same Connection, screens and rules
```

- **Why a mirror of `gui_backend`** and not the telemetry packets above: the
  cockpit reads `gui_backend`'s streams (pico, pre-flight, alarms, mission,
  link profile…). Mirroring them means every panel, age and readiness verdict
  is the boat's own. `gui_mirror_node` connects as a normal client,
  subscribes and answers pings, and never sends a command.
- **Read-only.** In the cloud build every command (mode, cut propulsion,
  run checks, missions) comes back refused: "Read-only cloud view: commands
  are only accepted from the GUI on the boat network." A badge on every
  screen says so.
- **Honest ages.** Data is about 2–3 s behind the boat (tested 2026-11-11).
  Ages are measured against Azure's clock. If the boat or its backend goes
  quiet, nothing new is delivered and the cockpit's own "Link lost: nothing
  received for N s" banner appears. It does not show a frozen screen as
  live.
- **Map tiles.** The map asks for `/tiles/...` as it does on the boat. The
  hosting config forwards those paths to OpenFreeMap and OpenSeaMap, the same
  public sources `gui_backend` proxies.

### On the boat

Start the stack with `enable_gui:=true enable_gui_cloud_mirror:=true`. It
uses the same connection string file as above. The parameters are under
`gui_cloud_mirror:` in `cloud_bridge_params.yaml`.

**Light profile (the default).** The cloud is a light viewer; the full record
stays on the Jetson (mission recorder, bags, local spool). Measured
2026-11-11 against the simulator:

| Blob | What | How often | Size |
|---|---|---|---|
| `gui/latest.json` | position, status, alarms, sonar, mode… | every 3 s | ~1.7 KB (gzip) |
| `gui/state.json` | track/coverage tail (300 pts), plan, backend hello | every 15 s or on change | ~6.4 KB (gzip) |
| `gui/telemetry/…/HH.jsonl` | one light line: vessel, heading, pico, power, link, sonar, mission, alarms | every 15 s (uploaded every 30 s) | ~3.8 KB/line |
| `gui/images/…jpg` | camera snapshot, 320 px | every 30 s | 3 KB sim, ~10–15 KB real |

**About 1.2 M Azure writes a month, roughly $6–8**, plus a few cents of
storage that grows about 1.5–2 GB a month. **Nothing is deleted
automatically**, by decision. A viewer polling `latest.json` costs about
$1 a month in reads if left open 24/7. On the boat's internet link this
is about 0.15 GB a day.

The live view refreshes every 3 s and must stay under 6 s, or the cockpit
reports link loss. The map's trail follows every 15 s; the boat marker
every 3 s. A problem with the history never holds back the live files. If
an hour's history blob in Azure doesn't match the local spool (for example
a wiped spool), the existing blob is kept and the hour continues in
`HH.<unix time>.jsonl` next to it.

`cloud_telemetry_node`'s own Azure upload (`azure_enabled`) is now off by
default: the mirror feeds the cockpit, and both together double the writes.

### Hosting the cockpit

Both hosts build `NAMIBIA_REDESIGNED_GUI` with `npm run build:cloud`. The
build reads `../src/asket_gui`, so the host needs the whole repository, which
both clone by default.

**Vercel:** New Project → import the repo → Root Directory
`NAMIBIA_REDESIGNED_GUI`. Leave "Include files outside the root directory"
on, which is the default. `NAMIBIA_REDESIGNED_GUI/vercel.json` sets the
build, the output and the `/tiles` rewrites. Production branch:
`Namibia_mission`.

**Render:** New → Blueprint → the repo. `render.yaml` at the repository root
describes the static site. Not deployed yet; Render's support for rewrites to
another site's URL is assumed from its docs, not tested.

**Giving people access**, two ways:

1. **Viewer link (recommended).** Deploy with no Azure variables. Send the team
   `https://<your-site>/#blob=…&sas=…`; `provision.py --cockpit-url
   https://<your-site>` prints it. Each browser remembers it after the first
   visit; someone with only the bare site address gets a sign-in box.
2. **Token baked in.** Set `VITE_AZURE_BLOB` (e.g.
   `https://<account>.blob.core.windows.net`) and `VITE_AZURE_SAS`
   (the `sas` value from the link) in the host's environment variables.
   Then **anyone who can open the site can read the telemetry**, because the
   token is in the published JavaScript. Fine for a public demo; for anything
   else use a Vercel password or Deployment Protection, or option 1.

To try the cloud build locally: `cd NAMIBIA_REDESIGNED_GUI && npm run
dev:cloud` and open `http://localhost:5174/#blob=…` with your viewer link.

## Limits, honestly

- The Wi-Fi reading is the **Jetson's own** `wlan0`. On the boat the shore
  link is the MikroTik over Ethernet, and its RSSI would need the MikroTik API.
- "Live" means seconds, not milliseconds: the page polls `latest.json` every
  2 s, and the boat uploads every 2 s.
- Sonar points are one ping's fan in the bridge's frame, not georeferenced,
  and the sonar's clock is unset until it has NTP.
- Cockpit mirror tested 2026-11-11 against the `gui_backend` simulator and the
  real storage account, in headless Chromium (screens, camera, refusal of
  Cut propulsion and Run checks, 2–3 s data age). Not yet run on the boat.
- Tested end to end against the Azurite emulator on 2026-11-11, with fake
  sensors, a 25 s storage outage, and the dashboard in Chromium. Not yet
  run against the real Azure account, and the static website step can't be
  tested in Azurite.
