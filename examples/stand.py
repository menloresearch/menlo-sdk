"""Put the robot in STAND: the actuators hold a standing pose, with no balancing.

Works from any robot mode. From DAMP, the robot must hang from its gantry hook with both feet
on the floor. From MOVE, hang it from its gantry hook or seat it on a stool or bench first:
STAND has no balance loop, and a free-standing robot tips over. stand() returns once the
robot is in STAND and armed. Sends STAND and nothing else. The robot balances only in
MOVE: run balance.py next. To stop first on your own rule, uncomment the two guard lines
(see guard.py).
Run: python examples/stand.py
"""

import sys

# from guard import guard  # optional: your own rule, see guard.py
from menlo.asimov import NotReadyError, Robot, WaitTimeoutError

with Robot().connect() as robot:
    # region main
    # guard(robot)  # optional: uncomment this and the import to stop on your rule
    try:
        # Returns once the robot reports STAND and has been upright for 0.5 s (armed): the
        # firmware enters MOVE only after that.
        robot.stand()
    except (NotReadyError, WaitTimeoutError) as exc:  # no live state, a fault, or not armed
        print(exc)  # what the robot reports and what to do
        sys.exit(1)
    print(f"robot mode {robot.get_state().mode.name}, armed {robot.armed}")
    # endregion
