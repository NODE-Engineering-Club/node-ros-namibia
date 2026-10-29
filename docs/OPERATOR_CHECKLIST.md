# Asket — operator checklist

One page, for the beach. Names in **bold** are what the GUI shows. The RC
transmitter is always the authority: **Ch8 down** stops the boat, whatever the
GUI says.

## 1. Before leaving the shore

- [ ] Kit: RC transmitter (charged), shore station on its tripod and powered, laptop, killswitch reachable, boat batteries charged.
- [ ] Open the GUI: **`http://10.10.20.10:8090`**. The top bar shows **Receiving**.
- [ ] **Alarms** panel → **Enable sound** (red alarms beep, amber ones never do).
- [ ] **Pre-flight** panel → **Run pre-flight**. Badge **GO** or **NO-GO**; red and amber rows say what to do.
- [ ] Top bar: **READY TO GO** (green), or **NOT READY: …** with the one thing to fix first.
- [ ] Geofence loaded: the map's dashed **Geofence** line is around today's area (file `/data/setup/geofence.yaml`). With the survey profile, the pre-flight row **Geofence** is ok.
- [ ] **Sonar** card → **Open SonarView** (new tab): sonar settings and sonar recording are done there.

## 2. Arming

1. **Ch8 to the middle** (manual only).
2. **Ch7 down, then up.** Always needed after the boat is powered up and after an RC loss: until then the boat stays disarmed even with Ch7 up.
3. **Boat** panel: **Armed**, mode **Manual**, **RC linked**, **Ch8 middle — manual only**.

## 3. Driving

- **Autonomous:** Ch8 **up** (**Ch8 up — autonomy allowed**), then **Autonomous** in the Boat panel (click twice: the second click confirms). The mode badge turns **Autonomous**. Refused while the pre-flight is NO-GO; the deliberate way past it is **I know it is NO-GO, force** (logged).
- **Back to manual:** **Manual** in the Boat panel, or **Drop to MANUAL**, or **Ch8 to the middle** (the Pico forces manual).
- **Emergency stop:** **Ch8 down** (cuts propulsion in the Pico), and the **killswitch**. Not a GUI button: **Drop to MANUAL** only hands control back to the transmitter, it does not stop the boat.

## 4. If…

| Situation | What the GUI shows | Do |
|---|---|---|
| **RC lost** | **NOT READY: RC transmitter link lost**, red alarm **RC link lost**, mode **Propulsion cut** | The Pico has already cut propulsion (< 0.5 s). Get back in range / transmitter on, then **Ch7 down then up** to re-arm (manual). Autonomous must be asked for again. |
| **Red alarm** | Beep, red line in **Alarms** with its remedy | Do what the remedy says. **Silence 5 min** stops the beep; a new red alarm beeps again. |
| **NOT READY** | **NOT READY:** followed by the reason | Fix that reason, then **Run pre-flight** again. |
| **Weak link** | **Weak link: reduced detail** (amber) or **Weak link: beacon only — position, mode and battery** | Less data, slower; the camera is a thumbnail every 5 s or nothing. Bring the boat closer or turn the tripod. The RC does not depend on this link. |
| **Outside the geofence** | Red alarm **N m outside the geofence** (amber **N m from the geofence** before) | Take it in manual (Ch8 middle) and drive back inside: Autonomous will not plan from outside, and nothing stops the boat crossing the line in manual. |
| **No data** | **NOT READY: no data from the boat**, **No data for …** | Everything on screen is old. Check the shore station; the RC still works in range. |

## 5. End of session

1. **Recording** panel → **Stop recording** (and stop recording in SonarView).
2. Plug a USB drive into the Jetson; **Details** → **Recording** → the mission → **Export to …**. Wait for it to finish.
3. Switch off in this order: **Ch8 down** → ESC battery off (killswitch / disconnect) → stop the stack (`sudo systemctl stop asket.service`) → `sudo poweroff` on the Jetson → boat power off → transmitter off **last**.
