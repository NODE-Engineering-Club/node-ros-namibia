# Stable USB device names — `/dev/pico`, `/dev/bms_pack1`, `/dev/bms_pack2`, `/dev/pixhawk`

Everything USB on the boat goes through one hub, so the kernel hands out
`/dev/ttyACM0`, `ttyACM1`… in whatever order the devices wake up. That order
changes between boots, and a BMS reader opening the Pico's port is a bad day.
`99-asket.rules` gives each device a fixed name, chosen by **its own USB serial
number**.

The rules file ships with placeholders (`CHANGE_ME_…`). It does nothing until
they are filled in from the real devices. Do this once per boat, on the
**Jetson host** (not inside the container).

## 1. Find each device's serial number

Plug in **one device at a time**, so there is no doubt which port is which.

```bash
# What just appeared?
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
sudo dmesg | tail -n 20

# Its identity (replace ttyACM0 with what appeared):
udevadm info --query=property --name=/dev/ttyACM0 \
  | grep -E '^(ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL_SHORT|ID_USB_INTERFACE_NUM|ID_MODEL)='
```

Copy `ID_SERIAL_SHORT` into the matching line of `99-asket.rules`:

| Link | Device | Placeholder |
|---|---|---|
| `/dev/pico` | Raspberry Pi Pico, firmware `pico-node_v4` (`ID_VENDOR_ID=2e8a`) | `CHANGE_ME_PICO_SERIAL` |
| `/dev/bms_pack1` | BMS reader on pack 1 | `CHANGE_ME_BMS_PACK1_SERIAL` |
| `/dev/bms_pack2` | BMS reader on pack 2 | `CHANGE_ME_BMS_PACK2_SERIAL` |
| `/dev/pixhawk` | Pixhawk 4 | `CHANGE_ME_PIXHAWK_SERIAL` |

While the Pixhawk is plugged in, note `ID_VENDOR_ID:ID_MODEL_ID` too:
`26ac:0032` is what PX4 enumerates as, `1209:5740`/`1209:5741` is ArduPilot.
That is one of the ways to answer "PX4 or ArduPilot?"
(docs/HARDWARE_DECISIONS.md, V1).

If two devices report the **same** serial (some cheap USB-serial adapters
do), match them by hub port instead: replace `ENV{ID_SERIAL_SHORT}=="…"` with
`ENV{ID_PATH}=="…"`, taken from the same `udevadm info` output. That then
depends on which hub socket the cable is in, so label the sockets.

## 2. Install the rules

```bash
sudo cp deploy/udev/99-asket.rules /etc/udev/rules.d/99-asket.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --action=add
```

Check:

```bash
ls -l /dev/pico /dev/bms_pack1 /dev/bms_pack2 /dev/pixhawk
# each should point at a ttyACMx, e.g.  /dev/pico -> ttyACM2
```

A link that does not appear means its line did not match. See what udev
saw for that port with:

```bash
udevadm test "$(udevadm info --query=path --name=/dev/ttyACM0)" 2>&1 | grep -iE 'asket|SYMLINK'
```

## 3. Using the names inside the container (Podman)

The links exist on the **host**. A container does not see them by default,
and there is one trap:

- `--device /dev/pico` works, but **Podman resolves the link once, when the
  container starts**, and passes the `ttyACMx` it pointed at then. If the Pico
  is unplugged, resets into its bootloader, or re-enumerates after a brown-out,
  it comes back as a different `ttyACMx` and the container keeps the old,
  dead one until it is restarted.
- So the Jetson deployment (`deploy/jetson/`, next step) instead mounts
  the host's live `/dev` and udev database into the privileged container:
  `-v /dev:/dev -v /run/udev:/run/udev:ro`. The links are then seen as they
  are now, including after a re-plug.

Then start the stack with the names:

```bash
ros2 launch bringup njord.launch.py \
  pico_port:=/dev/pico \
  enable_bms:=true            # bms_port / bms2_port already default to /dev/bms_pack1 / 2
```

`pico_port` still defaults to `/dev/ttyACM0` in `njord.launch.py`: the Pico
path is part of the motor chain, and changing its default is Phase B. The
Pixhawk link (`fcu_url`) is Phase B too.
