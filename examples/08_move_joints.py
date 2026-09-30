"""Turn the head 0.3 rad to the left and back with goto(), then damp.

The robot must be supported (held, craned or on its stand): joint control turns the walking
policy off, so nothing balances it, and the example ends in DAMP.
Run: python examples/08_move_joints.py --supported
"""

import argparse

from menlo.asimov import Mode, Robot

parser = argparse.ArgumentParser(description="Move one joint with goto().")
parser.add_argument("--supported", action="store_true", help="confirm the robot is supported")
args = parser.parse_args()
if not args.supported:
    parser.error("joint control needs the robot supported; pass --supported to confirm")

with Robot().connect() as robot:
    if robot.state.mode is Mode.DAMP:
        robot.wait_ready("stand")
        robot.stand()
        robot.wait_for(Mode.STAND)
    robot.wait_ready("trajectory")

    # region goto
    start = robot.state.joint_pos
    target = list(start)
    target[robot.info.joint_index("Neck_Yaw")] += 0.3
    # From the current pose, over 2 s; returns once every joint is within 0.01 rad.
    robot.goto(target, duration=2.0, tolerance=0.01)
    print(f"Neck_Yaw at {robot.state.joint('Neck_Yaw').pos:.2f} rad")
    robot.goto(start, duration=2.0, tolerance=0.01)
    # endregion

    # region finish
    # Joint control ends in DAMP, with the robot still supported. Asimov Edge puts the
    # robot in DAMP 2 s after the last setpoint in any case; damp() does it now.
    robot.damp()
    robot.wait_for(Mode.DAMP)
    # endregion
