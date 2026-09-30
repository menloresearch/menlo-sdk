"""Stand the robot up from DAMP and wait until it is armed. Sends STAND and nothing else.

The robot must be on its feet, hanging from its gantry hook. STAND holds a fixed pose with
no balance loop; the robot balances only once it walks (MOVE). Run walk.py next.
Run: python examples/stand.py
"""

from check import require_ready
from menlo.asimov import Mode, Robot

ARM_TIMEOUT_S = 5.0  # the firmware arms after 0.5 s upright in STAND

with Robot().connect() as robot:
    require_ready(robot, "stand")  # fresh state, no latched fault, battery and actuators ok

    # region main
    if robot.state.mode is Mode.DAMP:
        robot.stand()
        robot.wait_for(Mode.STAND)
    else:
        print(f"the robot is already in {robot.state.mode.name}; nothing sent")
    # Armed: STAND held upright for 0.5 s. Until then the firmware does not enter MOVE.
    robot.wait_ready("move", timeout=ARM_TIMEOUT_S)
    print(f"robot mode {robot.state.mode.name}, armed {robot.armed}")
    # endregion
