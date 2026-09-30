"""Put the robot in STAND: the actuators hold a standing pose, with no balancing.

The robot must hang from its gantry hook with both feet on the floor. stand() returns once
the robot is in STAND and armed. Sends STAND and nothing else. The robot balances only in
MOVE: run balance.py next.
Run: python examples/stand.py
"""

import sys

from menlo.asimov import NotReadyError, Robot, WaitTimeoutError

with Robot().connect() as robot:
    # region main
    try:
        # Checks the robot first. Returns once it reports STAND and has been upright for
        # 0.5 s (armed): the firmware accepts MOVE only after that.
        robot.stand()
    except (NotReadyError, WaitTimeoutError) as exc:
        print(exc)  # what is wrong and what fixes it
        sys.exit(1)
    print(f"robot mode {robot.get_state().mode.name}, armed {robot.armed}")
    # endregion
