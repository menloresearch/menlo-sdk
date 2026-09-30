"""Act the moment a joint passes an angle, while set_joints() is still moving it.

set_joints(wait=False) starts a DURATION_S move of the left elbow and returns at once;
wait_until() then blocks until a state sample shows the elbow past PASS_RAD, and returns that
sample. The script acts on what the robot reports, mid-move, not on a timer. Moves joints
only. Run stand.py first: the script refuses unless the robot reports STAND. The robot must
stay supported, hanging from its gantry hook or seated on a bench: joint control turns the
walking policy off, so nothing balances the robot, and a free-standing robot in MOVE falls.
When the script ends it stops sending setpoints, and the robot is put in DAMP 2 s after the
last one. Set ROBOT_SUPPORTED to True once the robot is supported.
Run: python examples/wait_until.py
"""

import sys
import time

from menlo.asimov import Mode, NotReadyError, Robot, WaitTimeoutError

ROBOT_SUPPORTED = False  # True once the robot hangs from its gantry hook or sits on a bench
JOINT = "L_Elbow"
TURN_RAD = 0.6  # the whole move; positive bends the elbow
PASS_RAD = 0.3  # act once the elbow has bent this far
DURATION_S = 3.0

if not ROBOT_SUPPORTED:
    sys.exit("Support the robot, then set ROBOT_SUPPORTED = True in this file.")

with Robot().connect() as robot:
    # region main
    state = robot.get_state()  # one sample: the pose to move from and back to
    # A trajectory in MOVE turns the walking policy off, and a free-standing robot falls.
    if state.mode is not Mode.STAND:
        print(
            f"robot mode {state.mode.name}: this script moves joints from STAND only. With "
            "the robot hanging from its gantry hook or seated on a bench, run stand.py first "
            "(from MOVE: damp.py, then stand.py)."
        )
        sys.exit(1)
    start, home = state.joint_pos, state.joint(JOINT).pos
    target = list(start)
    target[robot.info.joint_index(JOINT)] += TURN_RAD
    try:
        robot.set_joints(target, duration=DURATION_S, wait=False)  # returns at once, moving
        began = time.monotonic()
        passed = robot.wait_until(lambda s: s.joint(JOINT).pos >= home + PASS_RAD, timeout=5.0)
    except (NotReadyError, WaitTimeoutError) as exc:  # not ready, faulted, or never got there
        print(exc)
        sys.exit(1)
    took = time.monotonic() - began
    print(f"{JOINT} at {passed.joint(JOINT).pos:.2f} rad after {took:.1f} s of {DURATION_S} s")
    # A new set_joints() ends the first move where it is: back, and wait until there.
    robot.set_joints(start, duration=DURATION_S)
    print(f"{JOINT} back at {robot.get_state().joint(JOINT).pos:.2f} rad")
    # endregion
# Leaving the with block stops the setpoints; the robot goes to DAMP 2 s later.
