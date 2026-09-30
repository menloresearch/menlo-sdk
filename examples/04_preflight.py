"""Check whether the robot is ready to stand, walk or run a trajectory. Sends nothing.

Connection mode: the saved robot's (`menlo setup`); MENLO_ROBOT=NAME picks another.
Run: python examples/04_preflight.py
"""

from menlo.asimov import Action, Mode, Robot

ACTIONS: tuple[Action, ...] = ("stand", "move", "trajectory")

# region preflight
with Robot().connect() as robot:
    for action in ACTIONS:
        print(robot.preflight(action))  # "ready to move", or one line per problem

    check = robot.preflight("move")
    if check.has("faulted"):
        print("The firmware latched DAMP. Restart it: nothing a script sends clears it.")
    elif check.has("wrong_mode") and robot.state.mode is Mode.DAMP:
        print("Stand the robot first: examples/05_stand_and_walk.py")
# endregion
