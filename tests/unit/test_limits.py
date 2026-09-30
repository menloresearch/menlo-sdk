"""The velocity clamp: defaults are the firmware caps, and a caller can move them."""

from __future__ import annotations

import pytest

from menlo.asimov import ConnectionConfig, Limits, Robot, UdpConfig, Velocity


def test_the_default_limits_are_the_firmware_caps():
    assert Limits() == Limits(vx=0.4, vy=0.4, vyaw=0.8)
    # 0.5 m/s is above what the firmware walks at, so the SDK says it clamped it.
    assert Velocity(0.5, 0.0, 0.0).clamped(Limits()) == Velocity(0.4, 0.0, 0.0)


def test_limits_above_the_caps_are_accepted_and_sent_as_asked():
    fast = Limits(vx=1.0, vy=1.0, vyaw=2.0)
    assert Velocity(0.9, 0.0, 1.5).clamped(fast) == Velocity(0.9, 0.0, 1.5)


def test_limits_parse_the_environment_form():
    assert Limits.parse("0.3, 0.2,0.6") == Limits(0.3, 0.2, 0.6)
    for bad in ("0.3,0.2", "a,b,c", "0.3,0.2,-1", "0.3,0.2,0.6,1"):
        with pytest.raises(ValueError):
            Limits.parse(bad)


def test_robot_limits_come_from_the_argument_then_the_environment_then_the_config(monkeypatch):
    cfg = ConnectionConfig(udp=UdpConfig("127.0.0.1"), limits=Limits(0.1, 0.1, 0.1))
    assert Robot(cfg).limits == Limits(0.1, 0.1, 0.1)
    assert Robot(ConnectionConfig(udp=UdpConfig("127.0.0.1"))).limits == Limits()
    monkeypatch.setenv("MENLO_LIMITS", "0.3,0.2,0.5")
    assert Robot(cfg).limits == Limits(0.3, 0.2, 0.5)
    assert Robot(cfg, limits=Limits(0.2, 0.2, 0.2)).limits == Limits(0.2, 0.2, 0.2)
    monkeypatch.setenv("MENLO_LIMITS", "fast")
    with pytest.raises(ValueError, match="MENLO_LIMITS"):
        Robot(cfg)
