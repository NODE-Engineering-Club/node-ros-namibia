import pytest

from asket_common.diagnostic_level import ERROR, OK, WARN, level_int


@pytest.mark.parametrize("value,expected", [
    (b"\x00", OK), (b"\x01", WARN), (b"\x02", ERROR), (0, OK), (2, ERROR), ("\x01", WARN), (b"", OK),
])
def test_level_int(value, expected):
    assert level_int(value) == expected


def test_the_trap_it_exists_for():
    # what rclpy delivers for an OK status is truthy, and int() refuses it
    assert bool(b"\x00") is True
    with pytest.raises(ValueError):
        int(b"\x00")
