"""Turn in place, end the turn early with stop(), and with --damp finish in DAMP.

The robot must be on its feet with clear floor around it for the turn. stop() ends a walk:
the robot stays in MOVE and balances. Pass --damp only with the robot supported (hanging
from its gantry hook or seated on a bench). Connection mode: the saved robot's.
Run: python examples/06_stop_and_shutdown.py [--damp]
"""

import argparse
import time

from menlo.asimov import Mode, Robot

parser = argparse.ArgumentParser(description="Stop a walk, and optionally damp the robot.")
parser.add_argument("--damp", action="store_true", help="damp at the end (robot supported)")
args = parser.parse_args()

with Robot().connect() as robot:
    if robot.state.mode is Mode.DAMP:
        robot.wait_ready("stand")
        robot.stand()
        robot.wait_for(Mode.STAND)
    robot.wait_ready("move")
    robot.set_velocity(vyaw=0.3, duration=5.0)
    time.sleep(2.0)

    # region stop
    robot.stop()  # cuts the 5 s hold short; the robot stays in MOVE, balancing
    robot.wait_for(Mode.MOVE)
    # endregion

    # region shutdown
    if args.damp:
        # DAMP makes every actuator compliant: a standing robot folds to the ground.
        # It is not an emergency stop; use the E-Stop in Asimov Manager for that.
        robot.damp()
        robot.wait_for(Mode.DAMP)
    # endregion
# Leaving the with block calls close(): zero velocity if one is held, then the link drops.
