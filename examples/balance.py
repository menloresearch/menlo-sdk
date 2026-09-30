"""Put the robot in MOVE at zero velocity: the walking policy balances it in place.

Run stand.py first. From STAND, balance() waits until the robot is armed, puts it in MOVE and
returns once the robot reports MOVE. In MOVE it ends any walk and sends zero velocity. The
robot must hang from its gantry hook with both feet on the floor. Run walk.py next.
Run: python examples/balance.py
"""

import sys

from menlo.asimov import NotReadyError, Robot, WaitTimeoutError

with Robot().connect() as robot:
    # region main
    try:
        # Checks the robot first when it is in STAND. Returns once it reports MOVE.
        robot.balance()
    except (NotReadyError, WaitTimeoutError) as exc:
        print(exc)  # what is wrong and what fixes it
        sys.exit(1)
    print(f"robot mode {robot.get_state().mode.name}, balancing in place")
    # endregion
