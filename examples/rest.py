"""Bring the robot to rest: MOVE, then STAND, then DAMP.

STAND has no balance loop, so a free-standing robot asked to stand from MOVE tips over; DAMP
makes every actuator compliant, so a standing robot falls. Support the robot before either:
hanging from its gantry hook or seated on a stool or bench. The script asks first, unless
YES is True, then sends STAND, waits until the robot reports it, sends DAMP and waits until
the robot reports that, printing each robot mode. A robot already in DAMP is left as it is.
Not an emergency stop: for that, use the E-Stop in Asimov Manager, or cut power at the
battery unit.
Run: python examples/rest.py
"""

import sys

from menlo.asimov import Mode, NotReadyError, Robot, WaitTimeoutError

YES = False  # True: rest without asking
STAND_TIMEOUT_S = 5.0  # how long to wait for the robot to report STAND

with Robot().connect() as robot:
    # region main
    mode = robot.get_state().mode
    print(f"robot mode {mode.name}")
    if mode in (Mode.DAMP, Mode.FAULT_DAMP):
        print("already at rest; nothing sent")
        sys.exit(0)
    if not YES:
        question = "Is the robot on its gantry hook or seated on a stool or bench? [y/N] "
        if input(question).strip().lower() not in ("y", "yes"):
            print("nothing sent")
            sys.exit(0)
    try:
        # STAND first: the robot holds its pose, so DAMP does not drop it from a walk.
        robot.stand(wait=False)
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=STAND_TIMEOUT_S)
        print(f"robot mode {robot.get_state().mode.name}")
        robot.damp()  # returns once the robot reports DAMP
    except (NotReadyError, WaitTimeoutError) as exc:  # no live state, a fault, or not reported
        print(exc)
        sys.exit(1)
    print(f"robot mode {robot.get_state().mode.name}")
    # endregion
