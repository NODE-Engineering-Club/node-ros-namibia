# NAMIBIA_REDESIGNED_GUI

The redesigned ASKET mission cockpit: the NODE branding (background pattern +
frosted-glass widgets) from the Figma file **GUI - Design**, on top of the
functional layer that already exists in this repository.

**Nothing outside this folder was modified.** The existing cockpit
(`src/asket_gui`) and backend (`src/gui_backend`) are untouched and remain what
the Jetson serves today.

```bash
cd NAMIBIA_REDESIGNED_GUI
npm install
npm run dev:mock   # whole cockpit, no backend, no boat → http://localhost:5174
npm run dev        # against a real gui_backend on :8080 (same proxy as asket_gui)
npm run build      # → NAMIBIA_REDESIGNED_GUI/dist (NOT gui_backend/static)
```

In mock mode, press **Mock: everything working** in the footer first (same as
the "Everything working" control in `asket_gui`'s dev panel): a fresh
simulator is disarmed and un-pre-flighted, which looks like a boat that is off.

## Architecture

```
Mission controls and documents/   →  WHAT the screens must do (requirements)
src/gui_backend                    →  WebSocket protocol, streams, commands, alarms, pre-flight
src/asket_gui/src/lib/**           →  Connection, mock transport, readiness, formatting  ┐ imported
src/asket_gui/src/panels/MissionMap.jsx → live map (GPS slot)                             ┘ via @asket
NAMIBIA_REDESIGNED_GUI/src         →  NEW presentation layer (this folder)
```

`vite.config.js` aliases `@asket` → `../src/asket_gui/src`. The redesign
**imports** the functional code instead of copying it, so the safety rules it
already enforces carry over unchanged:

* **Displayed state is confirmed state** (commands.py rule 4): buttons show the
  server's pending / confirmed / failed, never what was asked for.
* **Two-step confirmation** (rule 5): `ConfirmAction` asks before sending.
* **Same readiness verdict** (`lib/readiness.js`): if the old cockpit says
  NOT READY, so does this one, for the same reason.
* **Same subscriptions and data age**: what was received, not what the socket
  believes; sensors that are not fitted are never subscribed.
* **Mock mode swaps the socket, not the app.**

### How the requirements map to screens

| Requirement (Mission controls and documents) | Screen | Data |
|---|---|---|
| M0 Harbour acceptance, go/no-go before every sortie (KELP_MISSIONS §2) | **Preflight** (Welcome) | `diagnostics` stream, `run_system_test` command |
| §4.7 Navigation monitor: live map, link/RTK/battery strip, camera, alarms | **Navigate** | `vessel`, `heading`, `pico`, `link`, `power`, `sonar`, `mission`, camera frames |
| §4.6 Alert rules | **Alerts** | backend `alarms` list + related pre-flight items; alarm sound from `lib/alarmSound.js` |
| Mission catalogue M1–M5 (routes, survey lines) | **Mission** (design) | `.json` import/export, planner basemap |
| M8 Post-mission offload | **Repo** | saved routes (browser library) + vessel recordings (`mission.missions`) |

### Folder layout

```
src/
  main.jsx              boot: real Connection or mock, same as asket_gui
  App.jsx               shell, hash routing (#preflight #navigate #mission #alerts #repo), subscriptions
  theme/tokens.css      design tokens = the Figma styles (ASKET/Glass/*, ASKET/Status/*, overlay)
  theme/app.css         glass, pills, buttons, shell
  theme/screens.css     per-screen layout
  theme/legacy-map.css  dark skin for the reused MissionMap, scoped to the GPS slot
  components/           Shell (sidebar, header, footer, splash), ui (GlassPanel, Pill, ConfirmAction…), Icons (from Figma)
  slots/GpsSlot.jsx     GPS window = slot for the live MissionMap
  slots/CameraSlot.jsx  OAK window = slot for the live camera feed (blanks a stopped feed)
  slots/PlannerMap.jsx  Mission Design basemap; refits to the imported .json
  screens/              Preflight, Navigate, MissionDesign, Alerts, Repo
  lib/vessel.js         useVessel(): everything the screens read, from the shared store
  lib/missionFile.js    parse / validate / measure / export mission .json
  lib/missionStore.js   saved-mission library (localStorage)
public/missions/walvis_survey_01.json   example mission
public/brand/           put pattern.jpg here (see below)
```

### Design tokens

`src/theme/tokens.css` is 1:1 with the Figma local styles. Change a style in
Figma → change the matching variable → every screen follows.

**Background pattern:** export the `Pattern image` layer of the
`ASKET/Background` component in Figma as JPG and save it as
`public/brand/pattern.jpg`. Until then a CSS approximation is drawn.

## Mission .json format

What **Export .json** writes, and what **Import .json** prefers:

```json
{
  "format": "asket-mission", "version": 1, "name": "WALVIS_SURVEY_01", "crs": "WGS84",
  "cruise_speed_ms": 1.5, "obstacle_policy": "stop",
  "sensors": { "sonar": false, "lidar": true, "camera": false },
  "waypoints": [{ "lat": -22.944, "lon": 14.482 }, { "lat": -22.940, "lon": 14.482 }]
}
```

Also accepted: `{"waypoints": [[lat, lon], …]}`, a bare array, or a GeoJSON
`LineString` / `FeatureCollection` (lon, lat). Non-numeric or out-of-range
coordinates reject the whole file, with the reason on screen.

## Not wired yet (honest gaps)

These are shown in the UI as disabled, with the reason, rather than faked:

1. **Send to vessel** — `gui_backend/core/commands.py` has no command that
   uploads a waypoint route. Needs a backend command (e.g. `upload_route`) and
   a ROS-side consumer (Nav2 waypoint follower). Then enable the two buttons
   (Navigate, Mission Design).
2. **Waypoint progress** (passed / current WP) — not in any stream yet.
3. **Saved routes** live in the browser (localStorage). A backend route store
   would replace `lib/missionStore.js` only.
4. **Camera controls** from the Figma (DETECT ON, CAM PWR) — no backend
   command exists; only the feed status is shown.
5. The live map keeps `asket_gui`'s light chart style inside the glass frame.
   A dark map style would be a change to `src/asket_gui/src/lib/mapStyle.js`,
   which this folder deliberately does not edit.

## Switching the Jetson over (later, team decision)

When the redesign is accepted, point the build at the backend's static dir
(`build.outDir: '../src/gui_backend/gui_backend/static'`) or copy `dist/`
there. Until then both cockpits can run side by side: `asket_gui` on :5173,
this one on :5174, against the same backend.
