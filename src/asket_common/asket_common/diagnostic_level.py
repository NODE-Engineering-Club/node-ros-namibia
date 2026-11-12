"""``diagnostic_msgs/DiagnosticStatus.level`` as an int, whatever rclpy hands over.

The field is a ``byte``, and rclpy delivers it as a one-byte ``bytes``
(``b'\\x00'`` for OK). Both obvious uses of it are wrong:

* ``if status.level:`` is always true — ``b'\\x00'`` is a non-empty bytes;
* ``int(status.level)`` raises ``ValueError``.

mission_recorder did both, and died on the first /diagnostics message that
arrived while a mission was recording. Use this instead.
"""

from __future__ import annotations

OK, WARN, ERROR, STALE = 0, 1, 2, 3


def level_int(value) -> int:
    """0 OK, 1 WARN, 2 ERROR, 3 STALE, from bytes, an int, or a 1-char str."""
    if isinstance(value, (bytes, bytearray)):
        return value[0] if value else OK
    if isinstance(value, str):
        return ord(value[0]) if value else OK
    return int(value)
