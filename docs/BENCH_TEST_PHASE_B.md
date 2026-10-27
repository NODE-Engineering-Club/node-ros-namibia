# Bench test — Phase B (a) Pico motor path, (b) pico_bridge, (c) pico-node_v5

Run this **before** the boat goes in the water with this branch. Two stages:
**ESC unpowered** (everything but the thrusters), then **ESC powered, props out
of the water**. Write the result of each step in the table at the end.

Safety, both stages: boat on a stand, thrusters clear of hands, cables and the
floor; one person on the transmitter, one on the laptop. T200 thrusters are
water-lubricated: out of the water, spin them **only in short bursts (a few
seconds) at low throttle**.

## 0. Flash pico-node_v5 from a Windows laptop

The Pico is unplugged from the Jetson and plugged into a Windows laptop for
this, so the stack on the Jetson does not need stopping (pico_bridge logs the
port as lost and reopens it when the Pico comes back). ESC power **off**.

1. Install the [Arduino IDE 2](https://www.arduino.cc/en/software) (Windows
   installer). The Pico needs no Windows driver: it appears as a USB serial
   port (`COMx`), or as a drive named `RP2350` in BOOTSEL mode.
2. *File → Preferences → Additional boards manager URLs*:
   `https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json`
3. *Tools → Board → Boards Manager*, search **Raspberry Pi Pico/RP2040/RP2350**
   (Earle F. Philhower), install **4.0.1 or newer**. 4.0.0 is the first release
   with RP2350 (Pico 2) support and 4.0.1 fixed its tool downloads; an older
   3.x core does not list the Pico 2 at all. The latest 4.x is fine.
4. *Tools → Board* → **Raspberry Pi Pico 2** (RP2350). A Pico 1 (RP2040):
   **Raspberry Pi Pico**.
5. *Tools → USB Stack* → **Adafruit TinyUSB** (the sketch uses it). `Servo`
   ships with the core.
6. Open `firmware/pico-node_v5/pico-node_v5.ino` (the folder keeps that name).
7. **Verify** (✓). This is the first real arduino-pico build of v5; if it
   fails, keep the full error text.
8. Plug the Pico into the laptop, pick its `COMx` in *Tools → Port*,
   **Upload** (→). No port: unplug, hold **BOOTSEL** while plugging back in,
   then Upload (the `RP2350` drive appears; the IDE writes to it).
9. *Tools → Serial Monitor*, 115200 baud: first line `[VER] pico-node 5`, then
   `STATE ver=5 … rclatch=1 … txdrop=0` lines (`rclatch=1` is normal at
   power-up: Ch7 has to be cycled).
10. Close the Serial Monitor, unplug the Pico from the laptop and plug it back
    into the Jetson's hub.

Rolling back: the same steps with `firmware/pico-node_v4/pico-node_v4.ino`,
**and** the pre-Phase B (c) software on the Jetson: this stack refuses to
drive a v4 Pico on purpose.

## 0b. Prepare

1. On the Jetson: udev links (`deploy/udev/README.md`) — `ls -l /dev/pico`.
2. Start the stack with the defaults (Pico path, GUI):
   `ros2 launch bringup njord.launch.py enable_gui:=true` (or
   `sudo systemctl start asket.service`). GUI at `http://<jetson>:8090`.
4. A terminal to watch the Pico: `ros2 topic echo /pico/status --field data`.

## 1. ESC unpowered (battery disconnected from the ESC rail)

| # | Do | Expect |
|---|---|---|
| 1.1 | Power the Pico with **Ch7 already high**, Ch8 middle | `STATE ver=5 … armed=0 … rclatch=1`, red light. **Does not arm** (v4 armed here). |
| 1.2 | Ch7 low, then high | `rclatch=0`, then `armed=1 mode=2`, yellow light, relay clicks (`relay=1`), 2 beeps |
| 1.3 | Ch8 low | `mode=1 armed=0 relay=0`, relay clicks open, red, long beep |
| 1.4 | `ros2 node list \| grep -E "pico_bridge\|actuator_driver"` | **only** `/pico_bridge` (a) |
| 1.5 | pico_bridge log (`journalctl -u asket.service` or the launch terminal) | `Pico firmware : v5, as expected` (b) |
| 1.6 | GUI → Pre-flight → Run | `Pico firmware version: v5, matching this software` |
| 1.7 | Ch8 high, Ch7 cycled low→high, GUI: request AUTO | `mode=3`, green light |
| 1.8 | `ros2 topic pub -r 10 /control/effort geometry_msgs/msg/Twist "{linear: {x: 0.2}}"` and `ros2 topic echo /pico_bridge/motor_cmd` | numbers (e.g. `0.200,0.200`), not `PING` (b) |
| 1.9 | **RC lost, receiver failsafe:** still AUTONOMOUS (1.7–1.8 running), switch the **transmitter off** | in < 0.5 s: relay clicks open, red, `mode=1 armed=0 rclatch=1 wantauto=0`; GUI arming reason "the RC link was lost…". Note `sbusfs=` (1 = the RP4TD reports failsafe; if it stays 0 the receiver stops sending, also covered) |
| 1.10 | **No auto re-arm:** transmitter back on, **Ch7 still high**, wait 10 s | stays `armed=0 rclatch=1`, relay open |
| 1.11 | GUI: request AUTO while latched | not applied (Pico answers `[NAK] MODE AUTO refused`), stays disarmed |
| 1.12 | Ch7 low, then high | `rclatch=0`, then `armed=1 mode=2` (MANUAL, not AUTONOMOUS) |
| 1.13 | **RC lost, SBUS silent:** AUTONOMOUS again (1.7), then **unplug the receiver's SBUS lead** | same as 1.9 (`sbusfs` irrelevant: no frames). Re-plug, repeat 1.10–1.12 |
| 1.14 | **Serial never blocks:** stop the stack (`sudo systemctl stop asket.service`), then hold the port open without reading it: `sudo sh -c 'sleep 60 < /dev/pico'` (opening the port raises DTR, so the Pico sees a host that stopped reading) | during those 60 s the RC still works: Ch8 low opens the relay at once; Ch7 low→high re-arms (relay clicks) |
| 1.15 | After the 60 s, restart the stack | `STATE … txdrop=N` with N > 0 (lines dropped while nobody read), everything else normal |
| 1.16 | Re-plug test: with the stack running, unplug the Pico USB 5 s, plug it back | bridge log: port lost, then within ~2 s `Pico connecte`, `Pico firmware : v5` (b) |
| 1.17 | Banner / pre-flight | `E-STOP feedback: DISABLED (not wired)`; pre-flight `E-stop power feedback` amber (GPIO20 not wired — expected) |

Optional (b): flash `pico-node_v4/` once — the bridge logs
`Pico firmware : v4, but this stack drives v5 only`, `/pico_bridge/motor_cmd`
shows only `PING`, AUTO is refused, the pre-flight fails. Flash v5 back.

## 2. ESC powered, props out of the water

Connect the battery to the ESC rail. Keep throttle low, bursts short.

| # | Do | Expect |
|---|---|---|
| 2.1 | Arm in MANUAL (Ch8 middle, Ch7 low→high) | relay closes, ESC start-up tones, **no spin for 2 s** (arm window) |
| 2.2 | Throttle a little forward, then yaw | both thrusters forward; yaw spins them opposite. Wrong way? `thr_invert`/`yaw_invert` (pico_bridge) and `THR_INVERT`/`YAW_INVERT` (firmware) |
| 2.3 | Ch8 low while spinning | thrusters stop at once, relay opens (ESC power-down tones) |
| 2.4 | AUTONOMOUS (Ch8 high, Ch7 cycled, GUI AUTO), `ros2 topic pub -r 10 /control/effort geometry_msgs/msg/Twist "{linear: {x: 0.15}}"` | both spin slowly forward |
| 2.5 | Stop the `ros2 topic pub` | neutral within ~0.5 s (bridge sends PING after 0.3 s, Pico neutral after 0.5 s); relay stays closed |
| 2.6 | Restart 2.4, then **transmitter off** | thrusters stop in < 0.5 s, relay opens |
| 2.7 | Transmitter on, Ch7 still high | nothing spins, even with `/control/effort` still published |
| 2.8 | Ch7 low→high | MANUAL; sticks work; autonomy only after a new AUTO request |
| 2.9 | **Jetson freezes** in AUTONOMOUS (2.4 running): `sudo pkill -STOP -f pico_bridge` | neutral within 0.5 s, autonomy withdrawn (`link=0`, MANUAL) after 0.6 s; the transmitter still drives in MANUAL. Then `sudo pkill -CONT -f pico_bridge` |

## Results

| Step | OK / not OK | Notes (`STATE` line, timing, sounds) |
|---|---|---|
| 1.1 | | |
| … | | |

Not covered here: the Nav2 survey profile (geofence, speed limit) needs GPS and
the Pixhawk link (Phase B d) — test it on the water at low speed after these
pass. GPIO20 E-stop feedback stays disabled until the divider is wired.
