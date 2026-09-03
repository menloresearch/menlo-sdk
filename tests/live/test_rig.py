"""Against a real robot or a menlo-studio rig (`menlo-studio up --container --sdk`).

Set ``ASIMOV_SDK_LIVE_HOST`` (e.g. ``127.0.0.1``) to run. These are the only tests that
prove the SDK moves a body; the unit suite proves it speaks the wire. Kept short and
observable: every step waits on the robot's own report, never on a sleep.

The sequence ends in DAMP on purpose. On the studio firmware a DAMP mid-session
suppresses the next STAND until the rig restarts, so run this last.
"""

from __future__ import annotations

import time

import pytest

from asimov_sdk import Mode, Robot, Unknown

pytestmark = pytest.mark.live


def test_stand_walk_stop_damp(live_host):
    with Robot.connect_direct(live_host, timeout=5.0) as robot:
        print(robot.info)
        assert robot.info.dof == 25
        assert robot.state.age_s < 0.5, "state should be streaming"

        sent = robot.stand()
        assert isinstance(sent.wait_outcome(), Unknown), "no outcome channel on this edge yet"
        s = robot.wait_for(Mode.STAND, timeout=15.0)
        print(f"STAND reported after {time.monotonic() - sent.sent_at:.2f}s; upright={s.upright}")
        assert s.upright is True

        robot.set_velocity(vx=0.2, duration=3.0)
        s = robot.wait_for(Mode.MOVE, timeout=5.0)
        assert s.upright is True
        time.sleep(3.5)  # the bounded hold ends by itself (SDK sends the zero)
        assert robot.state.mode is Mode.MOVE, "zero velocity keeps the firmware in MOVE, at rest"
        assert robot.state.upright is True

        robot.stand()  # MOVE at rest -> STAND posture is an explicit request
        robot.wait_for(Mode.STAND, timeout=8.0)
        robot.set_velocity(vx=0.2)
        robot.wait_for(Mode.MOVE, timeout=5.0)
        robot.stop()
        time.sleep(1.0)
        assert robot.state.upright is True

        robot.damp()
        s = robot.wait_for(Mode.DAMP, timeout=5.0)
        print(f"DAMP reported; gravity={s.gravity}")
