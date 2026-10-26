"""Which Pico firmware is on the other end of the serial link, and what may be sent to it.

ROS-free, so ``pico_bridge`` (``src/control``) and the tests share one answer.

``pico_bridge`` is the only thing that writes to the Pico. Before Phase B it
read the version the Pico announces and only logged it: a Pico flashed with
anything else was driven anyway, with motor commands and ``MODE AUTO`` whose
meaning on that firmware nobody had checked. The case that matters now is a
Pico still on ``pico-node_v4``, which keeps the boat armed when SBUS goes
silent in AUTONOMOUS (``firmware/pico-node_v5/``).

So the bridge forwards **motion** — motor commands and ``MODE AUTO`` — only to
a Pico that has reported exactly :data:`EXPECTED_FIRMWARE_VERSION`. Until then,
and on any other version:

* a motor command goes out as ``PING``: the heartbeat stays, the thrusters do
  not move (the firmware holds neutral without fresh commands);
* ``MODE AUTO`` is not sent at all;
* ``MODE MANUAL`` is always sent. It only ever takes authority away.

The version is read from either place the firmware puts it: the ``[VER]
pico-node N`` banner at boot, and the ``ver=N`` first field of every ``STATE``
line. The STATE lines matter most — the bridge usually opens the port long
after the banner went past.
"""

from __future__ import annotations

import re

#: The firmware this stack drives. Pinned to ``FW_VERSION`` in
#: ``firmware/pico-node_v5/pico-node_v5.ino`` and to the GUI parser and the
#: pre-flight by ``test_pico_link.py``.
EXPECTED_FIRMWARE_VERSION = 5

_STATE_VER = re.compile(r"^STATE\s+ver=(\d+)(?:\s|$)")
_BANNER_VER = re.compile(r"^\[VER\]\s+\S+\s+(\d+)\s*$")


def version_of(line: str) -> int | None:
    """The firmware version a line announces, or ``None`` if it announces none.

    A ``STATE`` line without ``ver=`` first (``asket_ec_pico``, pre-v4) says
    nothing about its version, and that is not the same as saying the right one.
    """
    if not isinstance(line, str):
        return None
    text = line.strip()
    match = _STATE_VER.match(text) or _BANNER_VER.match(text)
    return int(match.group(1)) if match else None


class FirmwareGate:
    """What the bridge may send, given what the Pico has said about itself."""

    def __init__(self, expected: int = EXPECTED_FIRMWARE_VERSION) -> None:
        self.expected = int(expected)
        #: The last version the Pico announced, ``None`` until it has.
        self.reported: int | None = None

    def observe(self, line: str) -> bool:
        """Read one line from the Pico. True when the announced version changed."""
        version = version_of(line)
        if version is None or version == self.reported:
            return False
        self.reported = version
        return True

    @property
    def ok(self) -> bool:
        return self.reported == self.expected

    def describe(self) -> str:
        if self.reported is None:
            return f"no firmware version reported yet (expected v{self.expected})"
        if self.ok:
            return f"v{self.reported}, as expected"
        return f"v{self.reported}, but this stack drives v{self.expected} only"

    def motor_line(self, left: float, right: float) -> str:
        """The line for a fresh motor command: the command, or a bare heartbeat."""
        if not self.ok:
            return "PING"
        return f"{left:.3f},{right:.3f}"

    def mode_line(self, request: str) -> str | None:
        """The line for a mode request, or ``None`` when it must not be sent.

        ``request`` is what ``/pico/mode_request`` carries: ``AUTO`` or
        ``MANUAL``, with or without a ``MODE `` prefix, any case.
        """
        word = str(request).strip().upper()
        if word.startswith("MODE "):
            word = word[len("MODE "):].strip()
        if word == "MANUAL":
            return "MODE MANUAL"
        if word == "AUTO" and self.ok:
            return "MODE AUTO"
        return None
