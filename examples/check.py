"""Check whether the robot is ready to stand, walk or run a trajectory. Sends nothing.

stand(), balance(), set_velocity(), trajectory() and set_joints() run this check themselves
before they send anything, and raise NotReadyError when it fails. set_velocity() also needs
the robot in MOVE: "ready to move" in STAND means balance() can put it there. preflight()
is the same check on its own, for a status display or a script that decides for itself.
Run: python examples/check.py
"""

import time

from menlo.asimov import Robot

with Robot().connect() as robot:
    # region main
    # The SDK counts the 0.5 s a robot in STAND must be upright to arm from its own samples.
    time.sleep(0.6)
    print(f"robot mode {robot.get_state().mode.name}, armed {robot.armed}")
    for action in ("stand", "move", "trajectory"):
        print(robot.preflight(action))  # "ready to move", or "not ready to move:" and why
    # endregion
