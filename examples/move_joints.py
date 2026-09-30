"""Bend the left elbow 0.3 rad and back with set_joints(). Moves joints only.

Run stand.py first. The robot must stay supported, hanging from its gantry hook or seated on
a bench: joint control turns the walking policy off, so nothing balances the robot. When the
script ends it stops sending setpoints, and the robot is put in DAMP 2 s after the last one.
Set ROBOT_SUPPORTED to True once the robot is supported.
Run: python examples/move_joints.py
"""

import sys

from menlo.asimov import NotReadyError, Robot, WaitTimeoutError

ROBOT_SUPPORTED = False  # True once the robot hangs from its gantry hook or sits on a bench
JOINT = "L_Elbow"
TURN_RAD = 0.3  # positive bends the elbow; its range is 0 to 2.44 rad
DURATION_S = 2.0  # time for each move

if not ROBOT_SUPPORTED:
    sys.exit("Support the robot, then set ROBOT_SUPPORTED = True in this file.")

with Robot().connect() as robot:
    # region main
    state = robot.get_state()  # one sample: the pose to move from and back to
    start = state.joint_pos
    home = state.joint(JOINT).pos
    target = list(start)
    target[robot.info.joint_index(JOINT)] += TURN_RAD
    try:
        # Checks the robot first, then moves from the current pose over DURATION_S and
        # returns within 0.05 rad of the target. set_joints() holds it until the next verb.
        robot.set_joints(target, duration=DURATION_S)
    except (NotReadyError, WaitTimeoutError) as exc:  # refused, or the joints did not follow
        print(exc)
        sys.exit(1)
    print(f"{JOINT} moved from {home:.2f} to {robot.get_state().joint(JOINT).pos:.2f} rad")
    robot.set_joints(start, duration=DURATION_S)
    print(f"{JOINT} back at {robot.get_state().joint(JOINT).pos:.2f} rad")
    # endregion
# Leaving the with block stops the setpoints; the robot goes to DAMP 2 s later.
