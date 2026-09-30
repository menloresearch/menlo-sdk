"""Against a real robot.

Set ``MENLO_SDK_LIVE_HOST`` (e.g. ``127.0.0.1``) to run. These are the only tests that
prove the SDK moves a body; the unit suite proves it speaks the wire. Kept short and
observable: every step waits on the robot's own report, never on a sleep.

The sequence ends in DAMP on purpose. A DAMP drops the robot; the fall latch
suppresses the next STAND until the rig restarts, so run this last.
"""

from __future__ import annotations

import time

import pytest

from menlo.asimov import Mode, Unknown
from tests.conftest import connect_udp

pytestmark = pytest.mark.live


def test_stand_walk_stop_damp(live_host):
    with connect_udp(live_host, timeout=5.0) as robot:
        print(robot.info)
        assert robot.info.dof == 25
        assert robot.get_state().age_s < 0.5, "state should be streaming"

        sent = robot.stand(timeout=15.0)  # returns once STAND is reported and armed
        assert isinstance(sent.wait_outcome(), Unknown), "no outcome channel on the UDP lane"
        s = robot.get_state()
        print(f"armed after {time.monotonic() - sent.sent_at:.2f}s; upright={s.upright}")
        assert s.mode is Mode.STAND and robot.armed is True and s.upright is True

        robot.set_velocity(vx=0.2, duration=3.0, wait=False)
        s = robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=5.0)
        assert s.upright is True
        time.sleep(3.5)  # the bounded hold ends by itself (SDK sends the zero)
        assert robot.get_state().mode is Mode.MOVE, (
            "zero velocity keeps the firmware in MOVE, at rest"
        )
        assert robot.get_state().upright is True

        # No stand() here: STAND is a stiffen with no balance loop, and a free-standing
        # robot asked for it after a walk tips over. A second walk starts from MOVE at rest.
        robot.set_velocity(vx=0.2, wait=False)
        robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=5.0)
        robot.balance()
        time.sleep(1.0)
        assert robot.get_state().upright is True

        robot.damp()  # returns once DAMP is reported
        print(f"DAMP reported; gravity={robot.get_state().gravity}")
