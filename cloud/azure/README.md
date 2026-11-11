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

## Limits, honestly

- The Wi-Fi reading is the **Jetson's own** `wlan0`. On the boat the shore
  link is the MikroTik over Ethernet, and its RSSI would need the MikroTik API.
- "Live" means seconds, not milliseconds: the page polls `latest.json` every
  2 s, and the boat uploads every 2 s.
- Sonar points are one ping's fan in the bridge's frame, not georeferenced,
  and the sonar's clock is unset until it has NTP.
- Tested end to end against the Azurite emulator on 2026-11-11, with fake
  sensors, a 25 s storage outage, and the dashboard in Chromium. Not yet
  run against the real Azure account, and the static website step can't be
  tested in Azurite.
