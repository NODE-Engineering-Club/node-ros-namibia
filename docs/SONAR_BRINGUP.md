# Sonar bringup — first connection of the Omniscan 3D

**For the whole team. No prior knowledge of the sonar assumed.**

The job, in order:

1. The sonar alone — does it power up, answer, and send data?
2. SonarView — does it see the sonar and record?
3. The mission GUI — does it report the sonar as alive, and does the
   *Open SonarView* button work?

**Status after 2026-11-06:** step 1 is done. With the transducer in a bucket, the
sonar pinged and `omniscan_bridge` decoded its point data and showed it in RViz
(§3a). **Still to do:** a real target or seabed, SonarView against this unit,
the mission GUI, and all of it on the Jetson. That session ran on a Windows
laptop, in the dev container.

Read §0 before touching anything. There is one way to damage this device in an
evening and it is in §0. **Closing the bridge does not stop the sonar pinging.**

---

## What is known about this sonar

Updated after the bench session of **2026-11-06**. The split below is the point
of this section: everything in the first two lists was measured on the device,
and nothing in the third has been. Do not promote a line from the third list
to the first two without a measurement to point at.

### VERIFIED — measured on the hardware, 2026-11-06

Transducer facing up in a shallow bucket, under about 10 cm of water.

| Fact | How it was established |
|---|---|
| **The stream on TCP 62312 is Ping Protocol** | Every frame passed its checksum; 1628 frames in 75 s, 0 invalid, 0 bytes discarded |
| **The OS3D_POINT_SET (3104) layout decodes** | 388 point sets, 0 length mismatches, 0 decode errors. Ranges stop at the reported 29.9 m range, and power is on the same scale as the sonar's own thresholds. This checks structure and plausibility, not accuracy: nothing has been measured against a known target |
| **An idle sonar still sends status** | Unprompted: an empty message 14 about once a second, and message 9 about every 5 s, JSON with `"type": "os3d_temperature"`. On 2026-10-28 the probe saw nothing in 8 s; why that differs is unexplained |
| **A pinging sonar also sends 3104, 3010, 504 and 2198** | Each at about 5 per second. 2198 is the mono profile, which `omniscan_bridge` ignores and nobody has decoded |
| **`~/start_pinging` starts it**, at 5 Hz and 29.9 m | The ping counter puts the first ping about 11 s after the bridge started; 149 pings in 29.8 s by the sonar's clock |
| **It keeps pinging with no client connected** | The ping counter ran at 5.0 Hz throughout about 14 minutes that included the bridge being stopped |
| **`~/stop_pinging` stops it** | Called by the operator. A listen-only client then received 28 of message 14 and 7 of message 9, and none of 3104, 3010, 504 or 2198. The port and the status stream stayed up |
| **A listen-only client receives point data** if the sonar is already pinging | 75 s with the bridge sending nothing |
| **Only one TCP client at a time** | With one connection open, a second is refused; with the first closed, the port opens again. Tested with plain TCP clients, not SonarView (§5) |
| **Its clock is not set** | `utc_msec` equals `pwr_up_msec`, its uptime, so timestamps read as 1970 and `/diagnostics` stays at error level. Expected until the NTP step in `docs/SETUP.md` |
| **The `ping_hz` field in END_PING_INFO is the acoustic frequency** | It reads 451127 while pinging at 5 Hz. The bridge now gets the rate from the ping counter and uptime |
| **Every point in the bucket is unclassified** (`pt_type` 0) | 67508 of 67508 points, so the boat's seabed-only filter shows nothing here. Use `publish_all_points` (§3a) |

### VERIFIED — measured on the hardware, 2026-10-28

| Fact | How it was established |
|---|---|
| The sonar answers on **192.168.2.21** | ping, and SonarView |
| The host sat on **192.168.2.1/24** and that worked | `ip addr add` as in §2 |
| **TCP 62312 is open**, and it is the data port | `nmap`; SonarView reports `CONNECTED, tcp://192.168.2.21:62312, Product: Omniscan 3D` |
| **TCP 51200 is closed** | `nmap`. This is the port Cerulean's SonarView documentation names for the 450 family |
| **TCP 7077 is closed** on the sonar | `nmap`. 7077 is SonarLink's port, on the host that runs it, not on the sonar |
| A **web interface on 80** and **SSH on 22** | `nmap` |
| The unit presents **two boards, AX and BX**, running firmware **V1.8.2** and **V1.7.2** | SonarView's device list |
| SonarView makes those boards talk **UDP** with the host on ports **51221** and **51222**, boards at **192.168.2.201** and **192.168.2.202** | observed traffic; control packets of 10 to 105 bytes |

So the transport question is settled, and the answer was a combination neither
source predicted: **our port, Cerulean's transport.** `omniscan_bridge` was
built around 62312 over UDP; Cerulean's documentation says TCP on 51200; the
device does TCP on 62312. `scripts/sonar_probe.py` failed on the night it was
needed because it probed UDP 62312 and TCP 51200 and never tried the one
combination that was live. It now tries TCP 62312 first.

### NOT VERIFIED — no measurement exists

| Open question | Why it is still open |
|---|---|
| **Whether the points are in the right place** | Nothing has been measured against a known target or seabed. In the bucket they are reverberation, and the mounting values are unmeasured |
| **Whether `pt_type` ever reports bottom** | Everything was unclassified in a bucket with no seabed. If it stays that way on real water, the boat's seabed-only filter publishes nothing |
| **Whether SonarView behaves like the plain TCP clients on 62312** | The second-client refusal has not been tried with SonarView. Procedure in §5 "Both at once?" |
| **The 2198 mono profile payload** | Seen on the wire, never decoded. `sonar_bridge_node` on `main` assumes a layout for it that has not been checked |
| **The clock offset in the field** | Depends on §0 of `docs/SETUP.md`, the sonar's NTP server, which has not been pointed at the Jetson |
| **All of this on the Jetson** | The 2026-11-06 session ran on a Windows laptop, through a dev container |

### The IP conflict, which is a decision nobody has taken

The sonar is on **192.168.2.21** and SonarView has it talking to the host on
**192.168.2.1** of that same `/24`. The boat LAN plan
(`docs/HARDWARE_DECISIONS.md` §2) is `10.10.20.0/24`, and in that plan **.21 is
the OAK-D-LR**. Three things are therefore in the air at once: which subnet the
sonar ends up on, who gets `.21`, and whether the boards' `192.168.2.201/.202`
traffic survives a move. Recorded verbatim, as raised:

> **Conflit IP :** OAK-D-LR prévue en .21 dans le plan 10.10.20.x ; le sonar
> est en 192.168.2.21 par défaut et SonarView le fait dialoguer sur
> 192.168.2.x avec l'hôte en .1. Décision réseau à prendre.

Until that decision is made, the code follows the measurement: the default
sonar address is `192.168.2.21` everywhere, `ASKET_SONAR_HOST` overrides it,
and the `10.10.20.x` plan is left exactly as it was.

---

## 0. Before you start

### Safety — the one that matters

> **Don't leave the Omniscan 450 pinging for long periods of time when out of
> the water.** If you do, the transmit transducer will heat up gradually, and
> could be damaged. **Several minutes at a time should be fine.**
>
> — Cerulean Sonar, [Omniscan 450 Quick Start](https://docs.ceruleansonar.com/c/omniscan-450/quick-start)

In practice:

- **Powered but not pinging is safe indefinitely.** Plugging it in and pinging
  it over the network does *no* acoustic transmission. Steps 1–2 of this guide
  are safe dry.
- **Pinging is what heats the transducer.** That starts when SonarView opens a
  session, or when something sends `OS3D_SET_PING_PARAMETERS` with pinging on.
- **The rule, every time: the transducer face goes in water before anything
  pings.** A builder's bucket or a storage crate is enough — it only has to
  cover the transducer face and the RX16 array, not the whole unit.
- **Once started, it keeps pinging until told to stop or powered off.** Closing
  the bridge, RViz or the terminal does not stop it (measured 2026-11-06). Before
  lifting it out of the water, call `~/stop_pinging` or cut the power.
- If you must ping dry to check it is alive, keep it under a minute and let it
  cool.

Water also makes the test *mean* something: in air there is nothing to echo
off, so you get noise, not a seabed.

### Other safety

- **Power off before connecting or disconnecting anything.** 15 W is not
  dangerous to you, but hot-plugging a sonar is a good way to kill it.
- **Never lift or carry the unit by the transducer or its cable.** The ceramic
  elements are long and thin and break.
- **Don't bend the housing.** Same reason.
- Water and mains electricity in the same room: keep the bucket away from the
  laptop and the power supply, and put the supply on a surface it cannot be
  pulled off.

### Hardware checklist

| Item | Notes |
|---|---|
| Omniscan 3D 450 SS Modular | The three pieces: RX16 receive array, 450 SS transducer, electronics module |
| Power supply, **12–30 V DC, ≥ 15 W** | 12 V 2 A bench supply is plenty. Note the ferrules: **stripped wires, no connector** |
| Ethernet: 4-pin JST-GH to RJ45 adapter | Comes in the box (2×) |
| 1 m Ethernet cable with 4-pin JST-GH | Comes in the box. Also carries the power pair |
| Jetson AGX Orin, powered, on the bench | |
| Laptop on the same Wi-Fi/hotspot as the Jetson | |
| Ethernet cable, Jetson ↔ sonar (or ↔ switch) | |
| MikroTik CSS610 switch | Optional, see §1b |
| **Bucket or crate of water** | Deep enough to cover the transducer face |
| Towel | |

Specifications used above: Cerulean,
[Omniscan 3D Specifications](https://docs.ceruleansonar.com/c/omniscan3d/technical-details/specifications)
(Power: 12–30 V, 15 W max when pinging; data interface 100BaseT Ethernet;
weight in air 1057 g) and
[What's In The Box](https://docs.ceruleansonar.com/c/omniscan3d/getting-started/whats-in-the-box).

### What "Modular" means

The Modular version is the kit, not a single sealed unit:

- **Omniscan 3D 450 RX16 receive array** — the 16-element bar that makes the 3D
  possible, with a removable Omniscan 450 SS adapter.
- **Omniscan 450 SS transducer**, 1 m shielded cable — the transmitter.
- **Omniscan 450 electronics module** — the brain. Mounts on M3 standoffs
  (8 standoffs and 4 screws are in the box). **This is the part that needs
  power and Ethernet.**
- Cables: 1 m Ethernet with 4-pin JST-GH connectors (Blue Robotics switch
  compatible, and carrying a second pair for power), 2× JST-GH→RJ45 adapters,
  JST-GH→JST-GH adapter and jumper, penetrators and O-rings.

**Where the 3D processing happens:** Cerulean's published documentation does
not state this explicitly either way. What we do know is that the device emits
`OS3D_POINT_SET` (message 3104) containing *angle, time of flight and relative
amplitude* per point — i.e. the beamforming that turns 16 elements into
bearings has already happened by the time the data leaves the box. Treat the
rest (georeferencing, meshing, display) as SonarView's job on the host. Do not
plan around the Jetson doing beamforming.

---

## 1. Wiring

### 1a. Sonar straight into the Jetson (recommended tonight)

```
  ┌────────────────────┐
  │  Power supply      │   12–30 V DC, ≥ 15 W
  │  (12 V 2 A is fine)│
  └─────┬────────┬─────┘
        │ +      │ −        bare ferrules on the Ethernet cable's
        │        │          second wire pair — check polarity twice
  ┌─────┴────────┴──────────────────┐
  │  Omniscan electronics module    │
  │                                 │──── 1 m cable, 4-pin JST-GH
  │  RX16 array ──┐                 │         │
  │  450 SS tx ───┘                 │         │
  └─────────────────────────────────┘         │
                                        ┌─────┴────────┐
                                        │ JST-GH→RJ45  │
                                        └─────┬────────┘
                                              │ Ethernet
                                     ┌────────┴─────────┐
                                     │ Jetson AGX Orin  │
                                     │  (eth0)          │
                                     └──────────────────┘

  Laptop ── Wi-Fi / hotspot ── Jetson     (no cable to the laptop)
```

Fewest things that can be wrong. Use this unless the switch is already set up.

### 1b. Through the CSS610 switch

```
  Sonar ──┐
          ├── CSS610 switch ── Jetson
  Laptop ─┘   (any ports)
```

Same addressing as 1a. The advantage is that the **laptop is on the wired
segment too**, which matters because **SonarView's device discovery is a
broadcast and Wi-Fi normally drops it**
([Device Discovery and IP Addressing](https://docs.ceruleansonar.com/c/sonarview/device-discovery-and-ip-addressing)).
If you plan to re-address the sonar tonight, use this layout.

### Power

The Ethernet cable from the electronics module carries **a second pair of wires
for power, ending in bare ferrules** — there is no plug. Strip nothing; just
land them in the supply's terminals.

- **12–30 V DC.** 12 V is fine and the safest choice on a bench.
- **15 W maximum while pinging**, so about 1.3 A at 12 V. A 2 A supply has
  margin.
- **Check polarity before switching on.** Reversed polarity is how this evening
  ends early.

**What you should see:** nothing. There is no status LED documented. The sonar
being alive is established over the network in §3, not by looking at it.

---

## 2. Network

The sonar is on **192.168.2.21**. That is measured, not inferred: it answered
there on 2026-10-28 and SonarView reported the same address. The figure this
guide used to print, 192.168.2.92, was a transcription from Cerulean's
documentation for another model and was simply wrong.

We go to the sonar rather than moving the sonar to us — fewer irreversible
steps, and the address has not been decided anyway (see "The IP conflict"
above).

### Give the Jetson an address on the sonar's network

On the Jetson, over SSH:

```bash
# See what the Ethernet interface is called, and what is already set
ip addr show

# Add a second address on the wired interface. ADD, not replace:
# this does not touch Wi-Fi, the hotspot, or any existing address.
sudo ip addr add 192.168.2.1/24 dev eth0

# Check it took
ip addr show eth0
```

Replace `eth0` with the real name if it differs (`enP8p1s0` and similar are
common on Jetsons).

**Why this is safe:** `ip addr add` adds an address to an interface that can
hold several. Your Wi-Fi, your hotspot and your SSH session are on different
interfaces and are untouched. Nothing is written to disk, so **a reboot undoes
it**.

**What you should see:**

```
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> ...
    inet 192.168.2.1/24 scope global eth0
```

### Find the sonar

```bash
ping -c 3 192.168.2.21
```

**What you should see:** three replies, under 1 ms.

Then confirm the port map, which is the thing that was wrong before:

```bash
sudo nmap -p- 192.168.2.21
```

**What you should see** — exactly this, from 2026-10-28:

```
22/tcp    open  ssh
80/tcp    open  http
62312/tcp open  unknown
```

If 62312 is missing, or 51200 or 7077 appear, the device or its firmware is not
the one this guide describes. Record it; `omniscan.yaml`'s `transport: "tcp"`
and `port: 62312` are set from that scan.

**If it doesn't work:**

| Symptom | Try |
|---|---|
| `Destination Host Unreachable` | Cable, power, and that you used the right interface name |
| `Network is unreachable` | The `ip addr add` did not take — re-run `ip addr show` |
| No reply at all, no error | The sonar may not be on 192.168.2.21. Scan: `sudo nmap -sn 192.168.2.0/24`, or `ip neigh` after a few pings. Expect the sonar on .21 and, once SonarView has run, its two boards on .201 and .202 |
| Still nothing | Move to the switch layout (§1b) and use SonarView's discovery from the laptop — it finds devices *regardless of their IP address* |

### Undoing it

```bash
sudo ip addr del 192.168.2.1/24 dev eth0
```

Or reboot. Nothing persists.

---

## 3. Test 1 — the sonar alone

**Sonar in the water for this step** (see §0).

### The quick check

From the Jetson, inside the repo:

```bash
python3 scripts/sonar_probe.py
```

It defaults to 192.168.2.21 now; `--host`, or `ASKET_SONAR_HOST`, for anything
else. It needs no ROS, no container and no GUI, and it checks five things:
ICMP, discovery, **TCP 62312** (the measured transport), optionally UDP 62312,
and the two ports we used to assume.

The output below is from 2026-10-28, and the probe has not been re-run since.
On 2026-11-06 a plain TCP client received status messages from the idle sonar
(message 14 and message 9), so expect them where this shows silence.

**What was seen with the transducer in air:**

```
1. On the network?
[  ok  ] ping 192.168.2.21

2. Answers discovery?
[  ok  ] discovery: 192.168.2.21 says '...'

3. TCP 62312 — the measured transport?
[  ok  ] TCP 192.168.2.21:62312 accepted a connection
[ FAIL ] TCP 62312: connected, and the sonar sent nothing in 8 s
         Expected with the transducer in air. ...
```

**That is a pass, not a failure.** The `FAIL` marker on the second line is the
script being literal about having received no data, and the text under it says
so.

**Water alone does not start it.** Point data only arrives once something has
told the sonar to ping (§3a). Close any other client first, because the port
takes one connection at a time. What the probe's output would mean:

| What the probe prints | What it means | What to do |
|---|---|---|
| `message 3104 (OS3D_POINT_SET) x18` | The sonar is pinging and the layout decodes, as on 2026-11-06 | Carry on to §3a |
| `N bytes arrived and none of them parsed as Ping Protocol` | Not what this unit sent on 2026-11-06. The firmware or the device may have changed | Capture it: `sudo tcpdump -i eth0 -n -w sonar.pcap host 192.168.2.21 and port 62312`, and bring the file back. Do not guess at the layout |
| `connected, and the sonar sent nothing` | Not what this unit did idle on 2026-11-06, though it matches 2026-10-28 | Note it and try §3a. Nobody has explained the difference between the two sessions |

### Why the earlier version of this guide sent you to the wrong place

It told you to expect silence on **UDP 62312**, and to also try **TCP 51200**
because Cerulean's documentation says, for the 450 family:

> "Omniscan 450 communicates with tcp, ip address must match the actual
> device settings, and 51200 is the required port number for Omniscan 450."
>
> — [Session Configurations / Multiple Omniscans](https://docs.ceruleansonar.com/c/sonarview/session-configurations/multiple-omniscans)

Neither was right. The live combination is **TCP on 62312** — our port,
Cerulean's transport — and because the old probe tested the two diagonals and
not that corner, it reported a dead sonar that was in fact perfectly alive.
Both are kept in the probe's step 5 as a cross-check: if 51200 ever opens, the
firmware has changed and we want to know.

### Nothing we run tells the sonar to ping on its own

Neither the probe nor `omniscan_bridge` sends anything to the sonar by default.
That is deliberate: the parameters that would start it also start the
transducer, a transducer driven in air can damage itself, and a launch file
runs when nobody is beside the boat. The bridge's `send_ping_parameters`
parameter is `false`, and the `~/start_pinging`, `~/stop_pinging` and
`~/set_ping_parameters` services only ever fire because a person asked.

### Watching raw packets

If you want to see bytes rather than trust a script:

```bash
sudo tcpdump -i eth0 -n host 192.168.2.21
```

**What you should see:** traffic in both directions once SonarView connects.
`Ctrl-C` to stop.

Once SonarView is running (§4), expect more than the one TCP conversation:
on 2026-10-28 the sonar's two boards (**192.168.2.201** and
**192.168.2.202**) also exchanged **UDP** with the host on ports **51221** and
**51222**, in control packets of 10 to 105 bytes. That traffic is between
SonarView and the hardware, nothing in this repository participates in it, and
nobody has decoded it. If you want it on record, capture the whole host rather
than one port:

```bash
sudo tcpdump -i eth0 -n -w sonar-session.pcap net 192.168.2.0/24
```

---

## 3a. See the data in RViz

**Sonar in the water for this step** (see §0). Close SonarView and anything else
on TCP 62312 first, because the port takes one client at a time.

The bridge and RViz, which listen only and do not start the sonar:

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch omniscan_bridge sonar_rviz.launch.py
```

Then, in a second terminal with the same two `source` commands, once somebody has
checked the transducer is covered:

```bash
ros2 service call /omniscan_bridge/start_pinging std_srvs/srv/Trigger
```

To stop, before closing anything:

```bash
ros2 service call /omniscan_bridge/stop_pinging std_srvs/srv/Trigger
```

Watch that the points stop, and cut the power if they do not. Do this while the
bridge is still running: the service goes away with it, and the sonar does not
stop on its own.

**What you should see in a bucket:** red points in a flat fan. They are **not
real objects**. On 2026-11-06, under 10 cm of water, the median range was 23 m
and the closest point 0.68 m, which can only be reverberation. Each ping is one
slice, so all points share one `x`, and 3D only builds up as the boat moves.

**What this launch file changes from the boat's setup, so do not read the
positions as real geometry:**

- `publish_all_points` is on, so unclassified points are shown too.
- Mounting comes from `mounting.yaml` alone, which is unmeasured.
- Vessel attitude is off.

**If everything is one colour,** a few very strong echoes are squashing the scale.
In `Sonar points`, turn off Autocompute Intensity Bounds and try Min `1e9`, Max
`1e10`, roughly the sonar's own thresholds on 2026-11-06.

**What healthy looks like** in `/sonar/status` and `/diagnostics`, from 75 s on
2026-11-06: `actual_ping_rate_hz` 5.00, `packet_loss_ratio` 0, `rx_chunks_dropped`
0, `bytes_discarded` 0. `/diagnostics` still reads error level until the clock is
set. That is the unset clock, not a data fault.

**To reach the sonar from a Windows laptop's dev container:** Windows needs the
address, not the container. In an Administrator PowerShell, using your adapter's
name:

```powershell
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.2.1 -PrefixLength 24
```

This persists across reboots. To undo it:

```powershell
Remove-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.2.1
Set-NetIPInterface -InterfaceAlias "Ethernet" -Dhcp Enabled
```

---

## 4. Test 2 — SonarView

### On the Jetson or on the laptop?

**Tonight: on the laptop, if the laptop can reach the sonar** (i.e. the switch
layout, §1b). Otherwise on the Jetson.

Why the laptop first: SonarView is a graphical tool you will be clicking in,
discovery needs to be on the wired segment, and tonight is about the sonar, not
about proving the Jetson can host a container. Running it on the Jetson is the
deployment plan and can wait until the sonar is known good.

**The Jetson can run it** — the image is published for `linux/arm64`, which is
the Jetson's architecture. Verified against Docker Hub tags for
`nicknothom/sonarview` (`linux/amd64`, `linux/arm64`, `linux/arm/v7`).

### Installing on the Jetson (Docker)

Cerulean's instructions
([SonarView / Docker](https://docs.ceruleansonar.com/c/sonarview/installation/docker)):

```bash
mkdir -p /usr/SonarView
cat > docker-compose.yml << 'EOF'
services:
  sonarview:
    image: nicknothom/sonarview:latest
    container_name: sonarview
    network_mode: host
    restart: unless-stopped
    volumes:
      - /usr/SonarView:/userdata
    environment:
      - ENABLE_OS3D=true
EOF
docker compose up -d
```

**`ENABLE_OS3D=true` is required for the Omniscan 3D.** Cerulean's example has
it `false`; that is for the 2D Omniscan. Getting this wrong is the most likely
reason the 3D view is missing.

**`network_mode: host` is required.** Device discovery cannot work without it.

#### With Podman instead of Docker

Podman can run this, with two differences:

```bash
podman run -d --name sonarview --network=host \
  -v /usr/SonarView:/userdata:Z \
  -e ENABLE_OS3D=true \
  --restart=unless-stopped \
  docker.io/nicknothom/sonarview:latest
```

- `--network=host` behaves differently under rootless Podman — **run this
  rootful (`sudo podman`)** or discovery broadcasts will not work.
- `:Z` relabels the volume for SELinux. Harmless if SELinux is not enforcing.
- `podman generate systemd` if you want it to survive a reboot.

**JetPack r35.4 / Ubuntu 20.04:** the image is `linux/arm64` and the container
brings its own userspace, so the host's Ubuntu version does not matter. What
matters is that Docker or Podman is installed and the kernel is recent enough —
r35.4 is kernel 5.10, which is fine for both. No CUDA, no GPU access needed.

### Starting it

Open from the laptop:

```
http://<jetson-ip>:7077
```

or `http://localhost:7077` if you installed it on the laptop.

**What you should see:** the SonarView interface.

**If it doesn't work:**

| Symptom | Try |
|---|---|
| Page does not load | `docker ps` — is the container up? `docker logs sonarview` |
| Loads but no devices | Click **Device Discovery**. If empty, you are probably not on the wired segment — discovery is broadcast and Wi-Fi drops it |
| Device shown with an **orange** IP | Its address is not on your subnet, so Connect is disabled. Either give yourself an address on its subnet (§2) or change the device's address from this screen |
| Device found but Connect fails | Firewall. Cerulean's docs suggest temporarily disabling it |

### Connecting and recording

1. **Device Discovery** → the Omniscan should appear.
2. **Connect**.
3. **Sonar in the water now** if it is not already. Pinging starts here.
4. You should see live data — ping view, side-scan, and the 3D view.
5. Record from the app bar.

**What you should see:** a seabed (or a bucket bottom) that changes when you
move the sonar. In a bucket, expect a strong close return and little else —
that is success, not failure. You are proving the chain works, not surveying.

### Where the files are

- In the container: `/userdata`
- On the host: `/usr/SonarView` (whatever you mapped)
- Format: **`.svlog`**, or **`.svlz`** (gzipped `.svlog`) from version
  1.14.20-beta onward. Long sessions are split across several files.
  ([Log Files](https://docs.ceruleansonar.com/c/sonarview/log-files))

Copy them to the laptop:

```bash
# run this ON THE LAPTOP
scp -r <user>@<jetson-ip>:/usr/SonarView/*.svlz ~/sonar-tonight/
```

To convert or export to another format, open the file in SonarView and use
**Export** in the left sidebar; drag a folder onto the window for several at
once
([Exporting Log Files](https://docs.ceruleansonar.com/c/sonarview/exporting-log-files)).

---

## 5. Test 3 — the mission GUI

### Deploy `hw-alignment` on the Jetson

```bash
# on the Jetson
cd ~/node-ros-namibia          # wherever the repo lives
git fetch origin
git checkout hw-alignment
git pull

# inside the ROS container
colcon build --symlink-install --packages-select \
    gui_backend omniscan_bridge system_test mission_recorder asket_common asket_interfaces
source install/setup.bash
```

If the GUI's web assets have changed you also need `cd src/asket_gui && npm
install && npm run build` before `colcon build`. If that fails tonight, see
§9 — it is not worth losing the evening over.

### Start sonar-test mode

```bash
ros2 launch gui_backend sonar_test.launch.py
```

This starts **only** `gui_backend`, `omniscan_bridge`, `system_test` and
`mission_recorder`. No Pico, no Pixhawk, no Nav2 — nothing else can get in the
way or raise alarms you then have to explain.

It defaults to **`sonar_host:=192.168.2.21`**, where the sonar actually
answered. The boat LAN address (`10.10.20.31`) is only a plan; nobody has moved
the sonar onto it. For any other address:

```bash
ASKET_SONAR_HOST=192.168.2.30 ros2 launch gui_backend sonar_test.launch.py
ros2 launch gui_backend sonar_test.launch.py sonar_host:=192.168.2.30
```

The bridge will **not** command the sonar to ping. If you want it to —
transducer in water, somebody standing there — add
`-p send_ping_parameters:=true` to the node, or use the GUI's sonar controls,
which go through the service.

> ### The pre-flight will say NO-GO. That is correct.
>
> There is no Pico, no GNSS fix and no heading, so the vessel genuinely is not
> fit to go anywhere, and a pre-flight that said GO would be the broken one.
> **Ignore the verdict and read the Sonar panel.**

### Open the GUI

From the laptop:

```
http://<jetson-ip>:8090
```

**What you should see:** the interface, a red NO-GO pre-flight, and a **Sonar**
card.

### What to check, in order

1. **Sonar card.** `sonar alive` (green) means `omniscan_bridge` is receiving
   *and decoding* `OS3D_POINT_SET`. The bridge has done that (§3a), but this card
   has not been tried against the real sonar yet.
   - `sonar silent` is the **expected** reading when the sonar is not pinging.
   - To tell apart the three states behind "silent", read `/diagnostics`
     rather than guessing:

     ```bash
     ros2 topic echo /diagnostics --once
     ```

     | Key | Reading | What it means |
     |---|---|---|
     | `link_up` | `False` | Not connected. Wrong address, or nothing listening. Check `last_connect_error` |
     | `link_up` / `bytes_received` | `True` / `0` | Connected, and the sonar is sending nothing. Dry transducer, or the stream has to be asked for |
     | `bytes_received` / `point_sets` | non-zero / `0` | Something is arriving and it is **not** what we parse. Check `frames_invalid` and `bytes_discarded`, then capture it with tcpdump |
     | `point_sets` | non-zero | It works. `last_point_utc_ms` is the sonar's own timestamp on the last one |

2. **Ping rate** is non-zero in the card's detail.
3. **Clock offset.** The sonar's clock against the Jetson's. If this is large,
   say so in the results sheet — every recorded ping is timestamped with it,
   and a wrong offset makes a survey un-georeferenceable after the fact.
4. **The "Open SonarView" button.** It reads its address from the **Setup**
   page. The default is `http://10.10.20.10:7077`, which is the Jetson's
   address *on the boat LAN* — a network that does not exist tonight.
   - Go to **Setup**, find **SonarView address**, and set it to where
     SonarView actually is, e.g. `http://192.168.2.1:7077` (the host's own
     address on the sonar's subnet, which is where it ran on 2026-10-28) or
     the Jetson's hotspot address. Note that 7077 is SonarLink's port **on the
     host running it** — it is closed on the sonar itself, which was confirmed
     by nmap.
   - Save. The button updates immediately.
   - Click it. It should open SonarView in a new tab.

### Both at once? — probably not; confirm with SonarView

**On 2026-11-06 the sonar refused a second TCP client on 62312 while another was
connected.** Expect SonarView and `omniscan_bridge` to be mutually exclusive, and
so the mission GUI's sonar panel to be off whenever SonarView is recording. That
is a design constraint, not a nuisance.

That test used plain TCP clients, not SonarView, which also talks to the boards
over UDP (§3). It is still worth the one deliberate test below.

`omniscan_bridge` opens one connection, sends nothing unless asked, and reports
what happens.

#### The test

Needs the **transducer in water** — otherwise neither client receives anything
and the test cannot distinguish "they coexist" from "both are silent anyway".

1. **SonarView alone.** Start it, connect, confirm live data and the 3D view.
   Leave it running.
2. **Add the bridge.** Start `sonar_test.launch.py` (§5) and watch **both**:
   - Does SonarView's session drop, or report an error?
   - Does the GUI's sonar card go to `sonar alive`, or does `/diagnostics` show
     `link_up: False` with `last_connect_error` naming a refusal?
   - Watch `connect_failures` and `disconnects` in `/diagnostics`: a device
     that alternates between the two clients shows up as those climbing
     together, which is worse than a clean refusal and easy to miss.
3. **Now the other order.** Stop both. Start the bridge first, confirm it has
   data, then start SonarView and connect. A device can refuse the second
   client without caring which one it is, or it can hand the stream to whoever
   asked last — those are different, and only this step tells them apart.
4. **Record which of four things happened:**

   | Outcome | What it means for the boat |
   |---|---|
   | Both receive data | Nothing to decide. Run them together |
   | The second client is refused | Exclusive. One of the two owns the sonar per mission, and the GUI's sonar panel is off whenever SonarView is recording |
   | The first client is cut when the second connects | Worse: whoever starts last silently wins. The GUI must then not be started during a recording |
   | Both connect and neither gets data | Something else is wrong. Go back to §3 |

#### Until then: the safe order

1. §3 probe alone → stop it.
2. §4 SonarView alone → **stop the session** (disconnect in the app).
3. §5 GUI alone.
4. Only then the test above, deliberately, with somebody watching both screens.

#### A note on the recordings from 28/10

If `/usr/SonarView` holds a `.svlz` from that evening, its **size** is worth a
look and that is all. The transducer was in air, so an empty or tiny file tells
you nothing about the protocol — not that the format is wrong, not that the
recording path is broken, not that the sonar is faulty. It is unreliable data
and should not be used to settle anything.

---

## 6. Getting the data off

| What | Where | How |
|---|---|---|
| SonarView recordings | `/usr/SonarView` on the host running it | `scp -r user@host:/usr/SonarView/*.svlz ~/sonar-tonight/` |
| `mission_recorder` output | `/data/missions` on the Jetson | `scp -r user@jetson:/data/missions ~/sonar-tonight/` |
| The probe's output | Terminal | Copy into the results sheet (§10) |

**Note:** `mission_recorder` only records a mission if one is started from the
GUI. In health-only mode there is no sonar data for it to record — the sonar
data is SonarView's, in `.svlz`. Do not expect the two to contain the same
thing.

Check a file arrived intact:

```bash
ls -lh ~/sonar-tonight/
file ~/sonar-tonight/*.svlz        # should say "gzip compressed data"
```

---

## 7. Shutting down cleanly

In this order:

```bash
# 1. GUI: Ctrl-C in the ros2 launch terminal

# 2. SonarView: disconnect the session in the app first, then
docker compose down          # or: sudo podman stop sonarview

# 3. Sonar out of the water, dry it, power off LAST
#    (power off while it is pinging is fine; it is not pinging by then)

# 4. Undo the Jetson's temporary address
sudo ip addr del 192.168.2.1/24 dev eth0
ip addr show eth0            # confirm it is gone

# 5. Back to the branch the Jetson was on, if you changed it
git checkout <previous-branch>
```

**Check Wi-Fi and the hotspot still work before you leave.** Nothing in §2
should have touched them, but check.

---

## 8. Troubleshooting

| Problem | Probable cause | What to do |
|---|---|---|
| No `ping` reply from the sonar | Power, cable, or wrong interface | Check polarity, `ip addr show`, try the other RJ45 adapter |
| `ping` works, TCP 62312 refuses | Sonar still booting, or not the device this guide describes | Wait a minute from power-on, then `sudo nmap -p- 192.168.2.21` and expect 22, 80, 62312 |
| TCP 62312 refuses, and it worked a moment ago | **Another client holds the port**: it takes one at a time. Check for a bridge, SonarView or probe still running | Close the other client and retry. Closing it does not stop the sonar pinging |
| TCP 62312 connects, only status arrives (messages 14 and 9) | **Normal: the sonar is idle** | Transducer in water, somebody present, then `~/start_pinging` (§3a) |
| TCP 62312 connects, nothing arrives | Seen on 2026-10-28, not on 2026-11-06; unexplained | Try §3a. If still silent, run §4 and re-probe while SonarView is live |
| TCP 62312 sends bytes that do not parse | Not what this unit sent on 2026-11-06. The firmware or the device may have changed | `sudo tcpdump -i eth0 -n -w sonar.pcap host 192.168.2.21 and port 62312` and bring the file back. Do not guess at the layout |
| 51200 or 7077 **open** on the sonar | Different firmware from 2026-10-28 | Record it; `omniscan.yaml`'s transport may need revisiting |
| SonarView finds no devices | Discovery is broadcast and Wi-Fi drops it | Get on the wired segment (§1b) |
| Device listed with an **orange** address | Not on your subnet | Give yourself an address on its subnet, or change the device's from that screen |
| SonarView connects, no 3D view | `ENABLE_OS3D` is `false` | Set it to `true`, recreate the container |
| SonarView page will not load | Container not running | `docker ps`, `docker logs sonarview` |
| GUI shows `sonar silent` | `omniscan_bridge` has no *decoded points* — which is not the same as no connection | `ros2 topic echo /diagnostics --once` and read `link_up`, `bytes_received`, `point_sets` (§5). They separate four different faults |
| GUI pre-flight NO-GO | **Expected on a sonar-only bench** | Ignore it; read the Sonar card |
| "Open SonarView" goes nowhere | Address still the boat LAN default | Setup page → SonarView address |
| RViz shows nothing, status looks fine | The boat's seabed-only filter: in a bucket every point is unclassified | Use `sonar_rviz.launch.py`, which sets `publish_all_points` (§3a) |
| SonarView session drops when the GUI starts | The device takes one client at a time, measured with plain TCP clients but not with SonarView | Stop one, then run the test in §5 "Both at once?" properly and record which of its four outcomes you got |
| Sonar does not ping when the bridge is running | By design: `send_ping_parameters` is `false` | Transducer in water, somebody present, then `~/start_pinging` (§3a), `-p send_ping_parameters:=true` or the GUI's sonar controls |
| `colcon build` fails | Missing deps, or frontend not built | §9 |
| Jetson lost its network | Wrong interface in `ip addr add` | `sudo ip addr del ...`, or reboot |

---

## 9. PLAN B — if the GUI will not deploy

**The value of the session does not depend on the GUI.** If `colcon build` fails or the
container will not start, stop trying at 30 minutes and bank these instead:

Still worth doing, in order of value:

1. **The sonar powers up and is reachable.** (§2) — proves power, cabling and
   the address.
2. **`scripts/sonar_probe.py` output, with the transducer in water.** (§3) —
   needs no ROS and no build. It only imports `omniscan_bridge.core.parser`,
   and degrades gracefully if even that is missing.
3. **Whether anything comes down TCP 62312, and whether it parses.** This is
   now the single most valuable result available, and the only one that cannot
   be obtained from a dry bench. The transport is settled; the **format** is
   not, and everything `omniscan_bridge` does with the stream rests on it.
   Three possible answers, all useful: point sets decode, bytes arrive that do
   not decode (capture them), or nothing arrives at all.
4. **SonarView sees the sonar and records.** (§4) — SonarView is independent
   of our stack entirely. If this works, the sonar is good and the survey is
   possible, whatever our software does.
5. **One `.svlz` file copied to the laptop.** (§6) — proves the recording path
   end to end. From a dry run its size proves nothing; see §5.
6. **Photos of the wiring**, especially the power ferrules, so the next person
   does not have to work it out again.

With 1–5 the evening is a success. The GUI is a convenience on top.

---

## 10. Results sheet

Fill this in as you go and send it back.

```
SONAR BRINGUP — <date>        people: ______________________

0. HARDWARE
   Power supply used (V / A):            ______________
   Powered up without smoke:             yes / no
   Water container used:                 ______________

1. WIRING
   Layout:                               direct / switch
   Jetson interface name:                ______________

2. NETWORK
   Jetson address added:                 ______________
   ping 192.168.2.21:                    ok / fail
   If fail, address actually found at:   ______________
   nmap -p- open ports:                  ______________
     (expected 2026-10-28: 22, 80, 62312)

3. SONAR ALONE
   *** TRANSDUCER IN WATER:              yes / no ***
       (if no, step 3 cannot settle anything about the data format)
   scripts/sonar_probe.py
     ping:                               ok / fail / skipped
     discovery answered:                 yes / no
     TCP 62312 accepted a connection:    yes / no
     bytes received on TCP 62312:        ______________
     TCP 62312 saw 3104 (OS3D_POINT_SET):yes / no
     51200 / 7077 open:                  yes / no  (both expected closed)
   *** THE FORMAT QUESTION:              3104 decoded /
                                         bytes but no 3104 /
                                         connected and silent /
                                         did not connect ***
   If "bytes but no 3104", pcap file name: ______________
   Paste the probe output:
   ______________________________________________________________

4. SONARVIEW
   Ran on:                               Jetson / laptop
   Docker or Podman:                     ______________
   ENABLE_OS3D:                          true / false
   Discovery found the sonar:            yes / no
   Connected:                            yes / no
   Live data visible:                    yes / no
   3D view worked:                       yes / no
   Recording made:                       yes / no
   File name(s) and size(s):             ______________
   Copied to laptop:                     yes / no

5. MISSION GUI
   colcon build:                         ok / failed  (if failed, error: ____)
   sonar_test.launch.py started:         yes / no
   sonar_host used:                      ______________
   GUI reachable at :8090:               yes / no
   Sonar card said:                      sonar alive / sonar silent / nothing
   /diagnostics link_up:                 true / false
   /diagnostics bytes_received:          ______________
   /diagnostics point_sets:              ______________
   /diagnostics frames_invalid:          ______________
   /diagnostics last_connect_error:      ______________
   Ping rate shown:                      ______________
   Clock offset shown:                   ______________ ms
   SonarView address set to:             ______________
   "Open SonarView" worked:              yes / no

6. BOTH AT ONCE   (only with the transducer in water; see §5)
   Tried:                                yes / no
   Order A - SonarView first, then bridge:
     SonarView stayed connected:         yes / no
     Bridge connected:                   yes / no
     Bridge last_connect_error:          ______________
   Order B - bridge first, then SonarView:
     Bridge stayed connected:            yes / no
     SonarView connected:                yes / no
   => Two clients at once:               both work /
                                         second refused /
                                         first cut /
                                         both silent /
                                         not tested

7. SHUTDOWN
   Temporary IP removed:                 yes / no
   Wi-Fi / hotspot still working:        yes / no
   Sonar dried and stored:               yes / no

8. NOTES — anything surprising, any error text, anything that took longer
   than it should have:
   ______________________________________________________________
   ______________________________________________________________
```

---

## Sources

- [Omniscan 450 Quick Start](https://docs.ceruleansonar.com/c/omniscan-450/quick-start) — out-of-water pinging warning
- [Omniscan 3D Specifications](https://docs.ceruleansonar.com/c/omniscan3d/technical-details/specifications) — power, interface
- [Omniscan 3D What's In The Box](https://docs.ceruleansonar.com/c/omniscan3d/getting-started/whats-in-the-box) — Modular contents
- [SonarView / Docker](https://docs.ceruleansonar.com/c/sonarview/installation/docker) — container, port 7077, `ENABLE_OS3D`
- [SonarView / Device Discovery and IP Addressing](https://docs.ceruleansonar.com/c/sonarview/device-discovery-and-ip-addressing) — discovery, broadcast and Wi-Fi, changing the address
- [SonarView / Log Files](https://docs.ceruleansonar.com/c/sonarview/log-files) — `.svlog` / `.svlz`
- [SonarView / Exporting Log Files](https://docs.ceruleansonar.com/c/sonarview/exporting-log-files)
- [SonarView / Session Configurations / Multiple Omniscans](https://docs.ceruleansonar.com/c/sonarview/session-configurations/multiple-omniscans) — TCP 51200 for the 450 family. **Measured closed on this device**; the live port is TCP 62312. Kept here because it is where the "TCP" half of the right answer came from
- [Docker Hub: nicknothom/sonarview](https://hub.docker.com/r/nicknothom/sonarview/tags) — arm64 availability

Everything in "VERIFIED" above has no source in this list, and that is the
point: it came off the hardware on 2026-10-28 and 2026-11-06, which is the only
authority that outranks a document.
