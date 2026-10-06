"""Put the robot in DAMP: every actuator stops holding its position, so a standing robot falls.

Only do this with the robot supported, hanging from its gantry hook or seated on a stool or bench.
damp() is not an emergency stop; for that, use the E-Stop in Asimov Manager, or cut power at
the battery unit. The script asks before it sends anything, unless YES is True.
Run: python examples/damp.py
"""

import sys

from menlo.asimov import Robot, WaitTimeoutError

YES = False  # True: damp without asking

with Robot().connect() as robot:
    # region main
    # damp() is never refused, so there is no check here, only the question.
    if not YES:
        mode = robot.get_state().mode.name
        question = f"Robot mode {mode}. Is the robot supported? Damp now? [y/N] "
        if input(question).strip().lower() not in ("y", "yes"):
            print("nothing sent")
            sys.exit(0)
    try:
        robot.damp()  # returns once the robot reports DAMP
    except WaitTimeoutError as exc:  # sent, but DAMP was not reported: the message says what next
        print(exc)
        sys.exit(1)
    print(f"robot mode {robot.get_state().mode.name}")
    # endregion
