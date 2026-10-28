# Sonar bringup — first connection of the Omniscan 3D

**For the whole team. No prior knowledge of the sonar assumed.**

Tonight's job, in order:

1. The sonar alone — does it power up, answer, and send data?
2. SonarView — does it see the sonar and record?
3. The mission GUI — does it report the sonar as alive, and does the
   *Open SonarView* button work?

Read §0 before touching anything. There is one way to damage this device in an
evening and it is in §0.

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
- **The rule for tonight: the transducer face goes in water before anything
  pings.** A builder's bucket or a storage crate is enough — it only has to
  cover the transducer face and the RX16 array, not the whole unit.
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

The sonar ships on **192.168.2.92** (the factory address our configuration
records; Cerulean's quick start does not print a default and refers you to
SonarView's discovery instead). Tonight we go to the sonar rather than moving
the sonar to us — fewer irreversible steps.

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
ping -c 3 192.168.2.92
```

**What you should see:** three replies, under 1 ms.

**If it doesn't work:**

| Symptom | Try |
|---|---|
| `Destination Host Unreachable` | Cable, power, and that you used the right interface name |
| `Network is unreachable` | The `ip addr add` did not take — re-run `ip addr show` |
| No reply at all, no error | The sonar may not be on 192.168.2.92. Scan: `sudo nmap -sn 192.168.2.0/24`, or `ip neigh` after a few pings |
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
python3 scripts/sonar_probe.py --host 192.168.2.92
```

This needs no ROS, no container and no GUI. It checks four things: ICMP,
discovery, the UDP data port, and TCP 51200.

**What you should see:**

```
1. On the network?
[  ok  ] ping 192.168.2.92

2. Answers discovery?
[  ok  ] discovery: 192.168.2.92 says '...'

3. Sending data?
[  ok  ] UDP 62312: message 3104 (OS3D_POINT_SET) x18
```

### Read this before you panic at step 3

**Expect silence on UDP 62312 at this stage, and do not treat it as a
failure.** Two independent reasons:

1. **The sonar probably streams only once a client asks it to.** Until
   SonarView (§4) opens a session, there may be nothing to hear. This is the
   normal case.

2. **The port itself is not confirmed.** Our `omniscan_bridge` is built around
   **UDP 62312**. That port appears **nowhere** in Cerulean's published
   documentation. What their documentation *does* say, for the 450 family, is
   the opposite:

   > "Omniscan 450 communicates with tcp, ip address must match the actual
   > device settings, and 51200 is the required port number for Omniscan 450."
   >
   > — [Session Configurations / Multiple Omniscans](https://docs.ceruleansonar.com/c/sonarview/session-configurations/multiple-omniscans)

   The probe tries both and tells you which answered. **Write down which one
   it was** — that is one of the most valuable results of the evening. If TCP
   51200 is the live transport, `omniscan_bridge` cannot work as configured
   and that is a code change, not a cabling problem.

### Watching raw packets

If you want to see bytes rather than trust a script:

```bash
sudo tcpdump -i eth0 -n host 192.168.2.92
```

**What you should see:** traffic in both directions once SonarView connects.
`Ctrl-C` to stop.

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

It defaults to **`sonar_host:=192.168.2.92`**, the factory address, because
that is where the sonar is tonight. If you re-addressed it:

```bash
ros2 launch gui_backend sonar_test.launch.py sonar_host:=10.10.20.31
```

> ### The pre-flight will say NO-GO. That is correct.
>
> There is no Pico, no GNSS fix and no heading, so the vessel genuinely is not
> fit to go anywhere, and a pre-flight that said GO would be the broken one.
> **Ignore the verdict tonight and read the Sonar panel.**

### Open the GUI

From the laptop:

```
http://<jetson-ip>:8090
```

**What you should see:** the interface, a red NO-GO pre-flight, and a **Sonar**
card.

### What to check, in order

1. **Sonar card shows `sonar alive`** (green). This means `omniscan_bridge` is
   receiving and decoding `OS3D_POINT_SET`.
   - `sonar silent` means nothing is arriving. Go back to §3 and note whether
     UDP 62312 or TCP 51200 answered.
2. **Ping rate** is non-zero in the card's detail.
3. **Clock offset.** The sonar's clock against the Jetson's. If this is large,
   say so in the results sheet — every recorded ping is timestamped with it,
   and a wrong offset makes a survey un-georeferenceable after the fact.
4. **The "Open SonarView" button.** It reads its address from the **Setup**
   page. The default is `http://10.10.20.10:7077`, which is the Jetson's
   address *on the boat LAN* — a network that does not exist tonight.
   - Go to **Setup**, find **SonarView address**, and set it to where
     SonarView actually is tonight, e.g. `http://192.168.2.1:7077` or the
     Jetson's hotspot address.
   - Save. The button updates immediately.
   - Click it. It should open SonarView in a new tab.

### Both at once?

**Do not run SonarView and `omniscan_bridge` against the sonar at the same time
until you have tested them separately.** We cannot tell you from the
documentation whether the device accepts two clients: Cerulean's API page does
not say, and if the real transport is a single TCP connection on 51200 then it
almost certainly does not.

**Safe order for tonight:**

1. §3 probe alone → stop it.
2. §4 SonarView alone → **stop the session** (disconnect in the app).
3. §5 GUI alone.
4. **Only then**, if everything above worked: start SonarView, confirm it is
   live, then start the GUI and watch. If the SonarView session drops when the
   GUI starts, they are exclusive — note it and stop. That is a real result.

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
| `ping` works, probe sees nothing on UDP 62312 | Sonar is not pinging yet, **or** the real transport is TCP 51200 | Normal before SonarView. Run §4, then re-probe. Record which port answered |
| SonarView finds no devices | Discovery is broadcast and Wi-Fi drops it | Get on the wired segment (§1b) |
| Device listed with an **orange** address | Not on your subnet | Give yourself an address on its subnet, or change the device's from that screen |
| SonarView connects, no 3D view | `ENABLE_OS3D` is `false` | Set it to `true`, recreate the container |
| SonarView page will not load | Container not running | `docker ps`, `docker logs sonarview` |
| GUI shows `sonar silent` | `omniscan_bridge` receiving nothing | Is the launch using the right `sonar_host`? Did SonarView grab the device? |
| GUI pre-flight NO-GO | **Expected tonight** | Ignore it; read the Sonar card |
| "Open SonarView" goes nowhere | Address still the boat LAN default | Setup page → SonarView address |
| SonarView session drops when the GUI starts | The device may accept one client | Stop one. Record it — it is a real finding |
| `colcon build` fails | Missing deps, or frontend not built | §9 |
| Jetson lost its network | Wrong interface in `ip addr add` | `sudo ip addr del ...`, or reboot |

---

## 9. PLAN B — if the GUI will not deploy

**Tonight's value does not depend on the GUI.** If `colcon build` fails or the
container will not start, stop trying at 30 minutes and bank these instead:

Still worth doing, in order of value:

1. **The sonar powers up and is reachable.** (§2) — proves power, cabling,
   and the factory address.
2. **`scripts/sonar_probe.py` output.** (§3) — needs no ROS and no build. Run
   it from a plain Python; it only imports `omniscan_bridge.core.parser`, and
   it degrades gracefully if even that is missing.
3. **Which port answered: UDP 62312 or TCP 51200.** This is the single most
   valuable number of the evening. It decides whether `omniscan_bridge` works
   at all, and it costs nothing to find out.
4. **SonarView sees the sonar and records.** (§4) — SonarView is independent
   of our stack entirely. If this works, the sonar is good and the survey is
   possible, whatever our software does.
5. **One `.svlz` file copied to the laptop.** (§6) — proves the recording path
   end to end.
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
   ping 192.168.2.92:                    ok / fail
   If fail, address actually found at:   ______________

3. SONAR ALONE   (sonar in water: yes / no)
   scripts/sonar_probe.py --host ...
     ping:                               ok / fail / skipped
     discovery answered:                 yes / no
     UDP 62312 saw 3104:                 yes / no
     TCP 51200 open:                     yes / no
   *** WHICH TRANSPORT ANSWERED:         UDP 62312 / TCP 51200 / neither ***
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
   Ping rate shown:                      ______________
   Clock offset shown:                   ______________ ms
   SonarView address set to:             ______________
   "Open SonarView" worked:              yes / no

6. BOTH AT ONCE   (only if 4 and 5 both worked)
   Tried:                                yes / no
   SonarView stayed connected:           yes / no
   GUI stayed "sonar alive":             yes / no
   => Two clients at once:               works / exclusive / not tested

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
- [SonarView / Session Configurations / Multiple Omniscans](https://docs.ceruleansonar.com/c/sonarview/session-configurations/multiple-omniscans) — TCP 51200 for the 450 family
- [Docker Hub: nicknothom/sonarview](https://hub.docker.com/r/nicknothom/sonarview/tags) — arm64 availability
