"""Put the robot in MOVE at zero velocity: the walking policy balances it in place.

Run stand.py first. From an armed STAND, balance() puts the robot in MOVE and returns once
the robot reports MOVE. In MOVE it ends any walk and sends zero velocity. In DAMP the
firmware does not enter MOVE, and balance() says so (stand() first). The robot must hang
from its gantry hook with both feet on the floor. guard(robot), from guard.py, stops the
script first when the robot reports what your rule will not drive through. Run walk.py next.
Run: python examples/balance.py
"""

import sys

from guard import guard
from menlo.asimov import NotReadyError, Robot, WaitTimeoutError

with Robot().connect() as robot:
    # region main
    guard(robot)  # your rule, in guard.py; delete this line to balance without it
    try:
        robot.balance()  # returns once the robot reports MOVE
    except (NotReadyError, WaitTimeoutError) as exc:  # no live state, a fault, or not MOVE
        print(exc)  # what the robot reports and what to do
        sys.exit(1)
    print(f"robot mode {robot.get_state().mode.name}, balancing in place")
    # endregion
