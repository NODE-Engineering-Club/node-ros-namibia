# NAMIBIA_REDESIGNED_GUI

The redesigned ASKET mission cockpit: the NODE branding (background pattern +
frosted-glass widgets) from the Figma file **GUI - Design**, on top of the
functional layer that already exists in this repository.

**Nothing outside this folder was modified.** The existing cockpit
(`src/asket_gui`) and backend (`src/gui_backend`) are untouched and remain what
the Jetson serves today.

**Read [`docs/GUI_DEVELOPER_GUIDE.md`](../docs/GUI_DEVELOPER_GUIDE.md) first**,
especially section D and section 3's "needs a review" table. This redesign
was brought in line with it; the section below says which rule is enforced
where.

```bash
cd NAMIBIA_REDESIGNED_GUI
npm install
npm run dev:mock   # whole cockpit, no backend, no boat → http://localhost:5174
npm run dev        # against a real gui_backend, through the dev proxy (below)
npm run build      # → NAMIBIA_REDESIGNED_GUI/dist (NOT gui_backend/static)
```

### Which backend `npm run dev` talks to

The dev server proxies `/ws`, `/api` and `/tiles` to `BACKEND_URL`, default
`http://localhost:8080` (the simulator: `python3 -m gui_backend.core.app --sim`).
The boat's `gui_backend` serves on **8090**, not 8080:

```bash
BACKEND_URL=http://<jetson-ip>:8090 npm run dev
```

### Mock mode

Open **◂ Mock controls** on the right edge: it is the same dev panel as
`asket_gui`'s (`@asket/components/MockControls.jsx`), so the whole section 6
checklist can be run here. Press **Everything working** first: a fresh
simulator is disarmed and un-pre-flighted, which looks like a boat that is off.

Under it, a **redesign test rig** (`src/lib/mockRig.js`, mock mode only) adds
two cases the shared mock cannot produce on its own:

* **Backend stops sending** (socket open, nothing arrives) and **Backend down**
  (socket closed, reconnects refused): checklist item 7. The mock's own "Link
  lost" fault keeps sending frames, so it never produces either.
* **Sonar panel as the boat sends it** (`sonar_panel: health`), with or without
  `omniscan_bridge` running: checklist item 8. The mock's `hello` never says
  `health`.

## Safety rules, and where each is enforced

From the developer guide, section 3. "Reused" means the component from
`src/asket_gui` is imported through `@asket` and rendered unchanged; the
redesign only adds the glass frame and scoped CSS (`src/theme/asket-embed.css`).

| Rule (guide §3) | Enforced by | How it appears here |
|---|---|---|
| Soft stop is "Cut propulsion", never the other words | **Reused** `panels/ModeCommands.jsx` (`ModeButtons`) + `lib/labels.js` | Navigate → Mode panel |
| Displayed state is confirmed state | **Reused** `ModeButtons`; `ConfirmAction` in `components/ui.jsx` shows the server's pending / confirmed / failed | Mode panel pill reads the `pico` stream ("Vessel: …"), never the request |
| Two-step confirmation | **Reused** `components/ConfirmButton.jsx` (via `ModeButtons`); `ConfirmAction` for mission commands | |
| NO-GO blocks AUTONOMOUS, force path deliberate and logged | **Reused** `ModeButtons` (all five steps) + backend `hub.py _nogo_gate()` | AUTONOMOUS stays pressable, fails with the reason, then checkbox + separate force button |
| Every live value shows its age, degrades visibly | **Reused** `components/Value.jsx` / `PanelAge`, `lib/staleness.js`, `streamAgeMs()` | Every number on Navigate; stale = amber, very stale = red + struck through (`theme/app.css`) |
| Absent ≠ good (killswitch / RC "not sent") | Navigate status tiles | "no data" when the stream never arrived, "not sent" when a field is null |
| Sonar states not collapsed further | **Reused** `panels/SonarPanel.jsx` | `health`: alive / silent / health not monitored; `full`: the panel with controls |
| Link loss visible as link loss | **Reused** `panels/LinkStatus.jsx`; `LinkBanner` in `App.jsx` | Banner on every screen + every panel greyed with a red edge |
| Subscription reasons on screen (guide 2.2) | `lib/streamNotes.js` + `StreamNotes` | "not sent — reason", "reduced to N Hz — reason" under each panel header |
| `stream_unavailable` ≠ refused (guide 2.2) | `lib/streamNotes.js` (taps the socket; the shared `Connection` ignores this frame) | "source silent — reason" |
| Motor tests never automatic | Not present in this GUI | — |

The GUI is not in the safety chain: the transmitter and the hardware
killswitch stop the boat, and the Mode panel and the link-loss banner both
say so.

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
  theme/asket-embed.css glass skin for the reused ModeButtons, SonarPanel, LinkStatus, MockControls (scoped)
  theme/responsive.css  tablet (<1100 px) and phone (<760 px) layouts, loaded last
  components/           Shell (sidebar, header, footer, splash), ui (GlassPanel, Pill, ConfirmAction, StreamNotes…), Icons (from Figma)
  components/Embed.jsx  AsketEmbed: wrapper for a reused asket_gui component (Details view on)
  slots/GpsSlot.jsx     GPS window = slot for the live MissionMap
  slots/CameraSlot.jsx  OAK window = slot for the live camera feed (blanks a stopped feed)
  slots/PlannerMap.jsx  Mission Design basemap; refits to the imported .json
  screens/              Preflight, Navigate, MissionDesign, Alerts, Repo
  lib/vessel.js         useVessel(): everything the screens read, from the shared store
  lib/missionFile.js    parse / validate / measure / export mission .json
  lib/missionStore.js   saved-mission library (localStorage)
  lib/streamNotes.js    subscription reasons + stream_unavailable ("source silent")
  lib/mockRig.js        mock-only test rig (backend stops / down, boat sonar view)
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

Still true after the developer-guide pass. These are shown in the UI as
disabled, with the reason, rather than faked:

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
6. **Start / stop pinging** — deliberately not in this GUI (guide section 5
   point 2). The sonar keeps pinging when the GUI closes; it is stopped with
   `ros2 service call /omniscan_bridge/stop_pinging std_srvs/srv/Trigger`.

## Needs a change outside this folder

Found while doing the guide pass; not done here because this folder must
not edit `src/`:

1. `src/asket_gui/src/lib/connection.js` drops `stream_unavailable` frames.
   The redesign works around it by tapping the socket (`lib/streamNotes.js`);
   the original cockpit still never says "source silent".
2. `SonarPanel.jsx` (full view) shows gain −1 as "auto" on its slider label but
   as "gain -1" in the "Apply to sonar" confirmation prompt.
3. The browser mock (`src/asket_gui/src/lib/mock`) never sends
   `sonar_panel: health` and its "Link lost" fault keeps sending frames, which
   is why the redesign carries its own test rig for checklist items 7 and 8.

## Switching the Jetson over (later, team decision)

When the redesign is accepted, point the build at the backend's static dir
(`build.outDir: '../src/gui_backend/gui_backend/static'`) or copy `dist/`
there. Until then both cockpits can run side by side: `asket_gui` on :5173,
this one on :5174, against the same backend.
