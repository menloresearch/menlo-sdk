"""Put the robot in DAMP: every actuator goes limp and a standing robot folds to the ground.

The robot must be supported: hanging from its gantry hook or seated on a bench. damp() is not
an emergency stop; for that, use the E-Stop in Asimov Manager, or cut power at the battery
unit. The script asks before it sends anything, unless YES is True.
Run: python examples/damp.py
"""

import sys

from menlo.asimov import Mode, Robot

YES = False  # True: damp without asking

with Robot().connect() as robot:
    # region main
    # DAMP is always allowed, so there is no preflight check here, only the question.
    if not YES:
        question = f"Robot mode {robot.state.mode.name}. Is the robot supported? Damp now? [y/N] "
        if input(question).strip().lower() not in ("y", "yes"):
            print("nothing sent")
            sys.exit(0)
    robot.damp()
    robot.wait_for(Mode.DAMP)
    print("robot mode DAMP")
    # endregion
