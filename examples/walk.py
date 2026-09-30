"""Walk: in MOVE the walking policy balances the robot and follows the velocity you send.

Here VX forward for DURATION_S seconds, then zero velocity: the robot stays in MOVE and keeps
balancing in place. Never changes the robot mode. Run stand.py and balance.py first. The
robot must hang from its gantry hook, with 2 m of clear floor ahead.
Run: python examples/walk.py
"""

import sys

from menlo.asimov import NotReadyError, Robot

VX = 0.3  # m/s forward; the firmware caps it at 0.4 m/s
VY = 0.0  # m/s to the left
VYAW = 0.0  # rad/s counter-clockwise; the firmware caps it at 0.8 rad/s
DURATION_S = 3.0

with Robot().connect() as robot:
    # region main
    try:
        # Checks the robot first: it must be in MOVE. Held and re-sent at 10 Hz for
        # DURATION_S, then zero velocity; returns after that.
        sent = robot.set_velocity(vx=VX, vy=VY, vyaw=VYAW, duration=DURATION_S)
    except NotReadyError as exc:  # nothing was sent, or (RobotFaultedError) a fault ended it
        print(exc)
        sys.exit(1)
    if sent.clamped:
        print("the SDK reduced the speed to the robot's limits:", sent.command)
    print(f"robot mode {robot.get_state().mode.name}, balancing in place")
    # endregion
